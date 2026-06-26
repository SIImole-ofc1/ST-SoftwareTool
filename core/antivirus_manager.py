"""Antivirus scanner backend — smart heuristic threat analysis with blocking."""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

# ── quarantine paths ──────────────────────────────────────────────────────────
_MODULE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUARANTINE_DIR = os.path.join(_MODULE_DIR, 'quarantine')
_MANIFEST      = os.path.join(QUARANTINE_DIR, 'manifest.json')


# ── threat data ───────────────────────────────────────────────────────────────

@dataclass
class ThreatEntry:
    severity:    str         # 'red' | 'orange' | 'green'
    category:    str
    path:        str         # display path
    reason:      str
    detail:      str  = ''
    threat_type: str  = 'file'    # file | process | registry | task | service
    # per-type blocking info
    file_path:   str  = ''        # actual FS path for file/process threats
    pid:         int  = 0
    reg_hive:    int  = 0         # winreg HKEY constant
    reg_key:     str  = ''
    reg_value:   str  = ''
    task_name:   str  = ''
    svc_name:    str  = ''
    blocked:     bool = False

    @property
    def severity_label(self) -> str:
        if self.blocked:
            return 'BLOCKED'
        return {'red': 'CRITICAL', 'orange': 'WARNING', 'green': 'CLEAN'}.get(
            self.severity, self.severity.upper()
        )

    def block_label(self) -> str:
        labels = {
            'file':     'Quarantine File',
            'process':  'Kill Process',
            'registry': 'Remove Registry Entry',
            'task':     'Disable Task',
            'service':  'Stop & Disable Service',
        }
        return labels.get(self.threat_type, 'Block Threat')


# ── PE analysis helpers ───────────────────────────────────────────────────────

def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = len(data)
    return -sum((f / n) * math.log2(f / n) for f in freq if f > 0)


_INJECT_APIS = [
    b'CreateRemoteThread', b'WriteProcessMemory',
    b'VirtualAllocEx',     b'NtCreateThreadEx',
]
_KEYLOG_APIS = [
    b'GetAsyncKeyState',  b'SetWindowsHookExA', b'SetWindowsHookExW',
]
_CRED_APIS = [
    b'MiniDumpWriteDump', b'LsaEnumerateLogonSessions', b'SamConnect',
]


# ── main manager class ────────────────────────────────────────────────────────

class AntivirusManager:

    _FAKE_SYSNAMES = {
        'svchost32.exe', 'svhost.exe',    'explorer32.exe', 'winlogon32.exe',
        'csrss32.exe',   'lsass32.exe',   'spoolsv32.exe',  'taskhost32.exe',
        'services32.exe','userinit32.exe','rundll.exe',      'svchst.exe',
        'lsas.exe',      'isass.exe',
    }

    _EXE_EXTS      = {'.exe', '.scr', '.dll', '.sys'}
    _SCRIPT_EXTS   = {'.bat', '.cmd', '.ps1', '.vbs', '.js', '.wsf', '.hta'}
    _CHECK_EXTS    = _EXE_EXTS | _SCRIPT_EXTS | {'.jar'}
    _DISGUISE_EXTS = _EXE_EXTS | _SCRIPT_EXTS

    _SCRIPT_KW = [
        b'powershell -enc', b'powershell -e ',   b'-encodedcommand',
        b'invoke-expression', b'iex(',            b'iex (',
        b'downloadstring',  b'net user /add',    b'net localgroup administrators',
        b'mimikatz',        b'meterpreter',      b'empire',
        b'reg add hklm',    b'reg add hkcu',     b'schtasks /create',
    ]

    _SAFE_APPDATA = {
        'microsoft', 'discord',   'slack',   'spotify',  'zoom',     'signal',
        'telegram',  'whatsapp',  'teams',   'chrome',   'firefox',  'edge',
        'opera',     'brave',     'vivaldi', 'onedrive', 'dropbox',  'google',
        'nvidia',    'amd',       'intel',   'steam',    'epic',     'ubisoft',
        'battle.net','blizzard',  'riot',    'origin',   'obs',
        'gimp',      'vlc',       'mpv',     '7-zip',    'winrar',   'notepad',
        'vscode',    'code',      'python',  'node',     'npm',      'git',
        'java',      'jetbrains', 'rider',   'pycharm',  'webstorm',
    }

    _SKIP_DIRS = {
        'windows\\winsxs', 'windows\\servicing', 'windows\\assembly',
        'windows\\installer', 'windows\\softwaredistribution',
        'perflogs', '$recycle.bin', 'system volume information',
    }

    _WINLOGON_KEY = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    _IFEO_KEY     = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
    _IFEO_TARGETS = {
        'svchost.exe', 'explorer.exe', 'lsass.exe', 'winlogon.exe',
        'csrss.exe', 'services.exe', 'smss.exe', 'wininit.exe',
        'taskmgr.exe', 'regedit.exe', 'cmd.exe', 'powershell.exe',
    }
    _RUN_KEYS = [
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
    ]

    def __init__(self):
        self._stop = False
        self._temp = os.path.expandvars('%TEMP%').lower().rstrip('\\')
        self._tmp  = os.path.expandvars('%TMP%').lower().rstrip('\\')
        self._app  = os.path.expandvars('%APPDATA%').lower()
        self._lapp = os.path.expandvars('%LOCALAPPDATA%').lower()
        # Directories where PE analysis is skipped during full scan (too many legit files)
        self._no_pe = tuple(filter(None, [
            os.path.expandvars('%WINDIR%').lower(),
            os.path.expandvars('%PROGRAMFILES%').lower(),
            os.path.expandvars('%PROGRAMFILES(X86)%').lower(),
        ]))

    def stop(self) -> None:
        self._stop = True

    # ── public scan API ───────────────────────────────────────────────────────

    def quick_scan(self, progress_cb=None, threat_cb=None) -> List[ThreatEntry]:
        """Targeted scan of high-risk locations + live processes + registry."""
        self._stop = False
        threats: List[ThreatEntry] = []

        for loc in [
            os.path.expandvars('%TEMP%'),
            os.path.expandvars('%TMP%'),
            os.path.expandvars('%APPDATA%'),
            os.path.join(os.path.expandvars('%LOCALAPPDATA%'), 'Temp'),
            os.path.expanduser('~/Downloads'),
        ]:
            if self._stop: break
            self._scan_dir(loc, quick=True, do_pe=True,
                           threats=threats, progress_cb=progress_cb, threat_cb=threat_cb)

        stages = [
            ('Scanning running processes…',       self._scan_processes),
            ('Scanning registry (Run keys)…',     self._scan_registry),
            ('Checking hosts file…',              self._scan_hosts),
            ('Scanning scheduled tasks…',         self._scan_tasks),
            ('Checking Windows services…',        self._scan_services),
        ]
        for msg, fn in stages:
            if self._stop: break
            self._emit(progress_cb, msg)
            for t in fn():
                threats.append(t)
                self._emit_threat(threat_cb, t)

        return threats

    def full_scan(self, progress_cb=None, threat_cb=None) -> List[ThreatEntry]:
        """Quick scan + full drive walk (PE analysis in user dirs only)."""
        self._stop = False
        threats: List[ThreatEntry] = []
        threats.extend(self.quick_scan(progress_cb, threat_cb))
        for drive in self._fixed_drives():
            if self._stop: break
            self._emit(progress_cb, f'Full scan — drive {drive}')
            self._scan_dir(drive, quick=False, do_pe=True,
                           threats=threats, progress_cb=progress_cb, threat_cb=threat_cb)
        return threats

    # ── directory walking ─────────────────────────────────────────────────────

    def _scan_dir(self, path: str, quick: bool, do_pe: bool,
                  threats: list, progress_cb=None, threat_cb=None) -> None:
        try:
            for root, dirs, files in os.walk(path):
                if self._stop: break
                low = root.lower()
                dirs[:] = [d for d in dirs
                           if not any(s in os.path.join(low, d.lower())
                                      for s in self._SKIP_DIRS)]
                if files:
                    self._emit(progress_cb, f'Scanning {root}  ({len(files)} files)')

                # Disable heavy PE analysis inside system/program dirs during full scan
                pe_here = do_pe and not any(low.startswith(p) for p in self._no_pe)

                for fname in files:
                    if self._stop: break
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in self._CHECK_EXTS and not quick:
                        continue
                    fpath = os.path.join(root, fname)
                    for t in self._check_file(fpath, fname.lower(), ext, pe_here):
                        threats.append(t)
                        self._emit_threat(threat_cb, t)

                if quick:
                    break   # top-level only for quick scan
        except (PermissionError, OSError):
            pass

    # ── per-file heuristics ───────────────────────────────────────────────────

    def _check_file(self, fpath: str, fname: str, ext: str,
                    do_pe: bool) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        pl = fpath.lower()

        # Double-extension disguise  e.g. invoice.pdf.exe
        parts = fname.split('.')
        if len(parts) > 2 and ext in self._DISGUISE_EXTS:
            return [ThreatEntry(
                severity='red', category='Double-Extension Disguise',
                path=fpath, file_path=fpath, threat_type='file',
                reason=f'Executable disguised as .{parts[-2].upper()} file',
                detail='Double extensions (invoice.pdf.exe) trick users into running malware.',
            )]

        # Fake system-process name
        if fname in self._FAKE_SYSNAMES:
            return [ThreatEntry(
                severity='red', category='Fake System Process Name',
                path=fpath, file_path=fpath, threat_type='file',
                reason='File name mimics a core Windows system process',
                detail='Legitimate Windows processes always reside in System32. A copy elsewhere is malware.',
            )]

        # Executable in %TEMP% → critical
        if ext in self._EXE_EXTS and (
            pl.startswith(self._temp) or pl.startswith(self._tmp)
        ):
            out.append(ThreatEntry(
                severity='red', category='Executable in Temp Directory',
                path=fpath, file_path=fpath, threat_type='file',
                reason='Executables in %TEMP% are a primary malware indicator',
                detail='Malware unpacks and runs from %TEMP% to evade detection. Quarantine immediately.',
            ))
            if do_pe:
                out.extend(self._analyze_pe(fpath))
            return out

        # Unknown executable in AppData → warning
        if ext == '.exe' and (pl.startswith(self._app) or pl.startswith(self._lapp)):
            if not any(s in pl for s in self._SAFE_APPDATA):
                out.append(ThreatEntry(
                    severity='orange', category='Unknown AppData Executable',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason='Unrecognized executable in AppData',
                    detail='Some portable apps live here legitimately; unknown ones warrant careful review.',
                ))

        # PE / binary analysis for executables
        if do_pe and ext in self._EXE_EXTS:
            out.extend(self._analyze_pe(fpath))

        # Script content analysis
        if ext in self._SCRIPT_EXTS:
            out.extend(self._check_script(fpath))

        return out

    # ── PE binary analysis ────────────────────────────────────────────────────

    def _analyze_pe(self, fpath: str) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            sz = os.path.getsize(fpath)
            if sz < 64 or sz > 64 * 1024 * 1024:
                return out
            with open(fpath, 'rb') as f:
                data = f.read(min(sz, 2 * 1024 * 1024))

            if data[:2] != b'MZ':
                return out

            # Shannon entropy of the sample
            ent = _entropy(data)

            # API string detection (import table is near the start of .idata section)
            inject = [a for a in _INJECT_APIS if a in data]
            keylog = [a for a in _KEYLOG_APIS if a in data]
            cred   = [a for a in _CRED_APIS   if a in data]

            # Process injection
            if len(inject) >= 2 or (inject and ent > 6.8):
                out.append(ThreatEntry(
                    severity='red', category='Process Injection Capability',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason=f'Injection APIs: {", ".join(a.decode() for a in inject)}',
                    detail=(
                        'CreateRemoteThread + VirtualAllocEx + WriteProcessMemory allow '
                        'injecting malicious code into other running processes.\n'
                        f'File entropy: {ent:.2f}/8.0'
                    ),
                ))
            elif inject:
                out.append(ThreatEntry(
                    severity='orange', category='Possible Process Injection',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason=f'Suspicious API: {inject[0].decode()}',
                    detail='Single injection API — may be a debugger or security tool. Review carefully.',
                ))

            # Keylogger
            if keylog:
                out.append(ThreatEntry(
                    severity='orange', category='Keylogger / Hook API',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason=f'Keyboard monitoring API: {", ".join(a.decode() for a in keylog)}',
                    detail='Can intercept all keystrokes system-wide. May be an accessibility tool or keylogger.',
                ))

            # Credential theft
            if cred:
                out.append(ThreatEntry(
                    severity='red', category='Credential Theft Tool',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason=f'Credential API: {", ".join(a.decode() for a in cred)}',
                    detail=(
                        'APIs like MiniDumpWriteDump and LsaEnumerateLogonSessions are '
                        'used by tools like Mimikatz to extract passwords from memory.'
                    ),
                ))

            # High entropy alone (packed/obfuscated) — only if no other finding
            if ent > 7.2 and not inject and not keylog and not cred:
                out.append(ThreatEntry(
                    severity='orange', category='Packed / Obfuscated Executable',
                    path=fpath, file_path=fpath, threat_type='file',
                    reason=f'Abnormally high entropy: {ent:.2f}/8.0  (threshold 7.2)',
                    detail=(
                        'High entropy means the file is packed, compressed, or encrypted. '
                        'Legitimate software rarely reaches this level — common in malware loaders.'
                    ),
                ))

        except (OSError, PermissionError, struct.error):
            pass
        return out

    # ── script content analysis ───────────────────────────────────────────────

    def _check_script(self, fpath: str) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            if not (0 < os.path.getsize(fpath) < 2_000_000):
                return out
            with open(fpath, 'rb') as f:
                content = f.read(8192).lower()
            for kw in self._SCRIPT_KW:
                if kw in content:
                    out.append(ThreatEntry(
                        severity='orange', category='Suspicious Script Content',
                        path=fpath, file_path=fpath, threat_type='file',
                        reason=f'Script contains: {kw.decode("utf-8", errors="replace")}',
                        detail='May be a legitimate admin script — review the file before quarantining.',
                    ))
                    break
        except (OSError, PermissionError):
            pass
        return out

    # ── process scan ──────────────────────────────────────────────────────────

    def _scan_processes(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    exe  = (proc.info.get('exe') or '').lower()
                    name = (proc.info.get('name') or '').lower()
                    pid  = proc.info.get('pid', 0)
                    if not exe:
                        continue
                    if exe.startswith(self._temp) or exe.startswith(self._tmp):
                        out.append(ThreatEntry(
                            severity='red', category='Process Running from Temp',
                            path=f'{name}  (PID {pid})',
                            file_path=exe, pid=pid, threat_type='process',
                            reason=f'"{name}" (PID {pid}) is executing from a Temp directory',
                            detail='Active malware almost always runs from %TEMP%. Kill and quarantine immediately.',
                        ))
                    elif name in self._FAKE_SYSNAMES:
                        out.append(ThreatEntry(
                            severity='red', category='Fake System Process Running',
                            path=f'{name}  (PID {pid})',
                            file_path=exe, pid=pid, threat_type='process',
                            reason=f'Running process mimics a Windows system process: {name}',
                            detail=f'PID {pid} — path: {exe}',
                        ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            pass
        return out

    # ── registry scan ─────────────────────────────────────────────────────────

    def _scan_registry(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []

        for hive, key_path in self._RUN_KEYS:
            hname = 'HKCU' if hive == winreg.HKEY_CURRENT_USER else 'HKLM'
            try:
                key = winreg.OpenKey(hive, key_path)
                i = 0
                while True:
                    try:
                        vname, value, _ = winreg.EnumValue(key, i)
                        vl = value.lower()
                        if vl.startswith(self._temp) or vl.startswith(self._tmp):
                            out.append(ThreatEntry(
                                severity='red', category='Startup Entry from Temp',
                                path=f'{hname}\\{key_path}  →  {vname}',
                                reg_hive=hive, reg_key=key_path, reg_value=vname,
                                threat_type='registry',
                                reason='Startup entry executes from Temp directory at every logon',
                                detail=f'Value: {value}',
                            ))
                        elif any(vl.endswith(e)
                                 for e in ('.ps1', '.vbs', '.bat', '.cmd', '.hta')):
                            out.append(ThreatEntry(
                                severity='orange', category='Script Startup Entry',
                                path=f'{hname}\\{key_path}  →  {vname}',
                                reg_hive=hive, reg_key=key_path, reg_value=vname,
                                threat_type='registry',
                                reason='Startup entry runs a script at every logon',
                                detail=f'Value: {value}',
                            ))
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except OSError:
                pass

        out.extend(self._check_winlogon())
        out.extend(self._check_ifeo())
        return out

    def _check_winlogon(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self._WINLOGON_KEY)
            for vname, expected in (('Shell', 'explorer.exe'), ('Userinit', 'userinit.exe')):
                try:
                    value, _ = winreg.QueryValueEx(key, vname)
                    if expected not in value.lower():
                        out.append(ThreatEntry(
                            severity='red', category='Winlogon Hijack',
                            path=f'HKLM\\{self._WINLOGON_KEY}  →  {vname}',
                            reg_hive=winreg.HKEY_LOCAL_MACHINE,
                            reg_key=self._WINLOGON_KEY, reg_value=vname,
                            threat_type='registry',
                            reason=f'Winlogon "{vname}" is modified: {value}',
                            detail=f'Expected "{expected}", found "{value}".\n'
                                   f'Classic malware persistence — runs at every logon.',
                        ))
                except OSError:
                    pass
            winreg.CloseKey(key)
        except OSError:
            pass
        return out

    def _check_ifeo(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self._IFEO_KEY)
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                    if sub_name.lower() in self._IFEO_TARGETS:
                        sub_key = winreg.OpenKey(key, sub_name)
                        try:
                            dbg, _ = winreg.QueryValueEx(sub_key, 'Debugger')
                            out.append(ThreatEntry(
                                severity='red', category='IFEO Debugger Hijack',
                                path=f'HKLM\\{self._IFEO_KEY}\\{sub_name}  →  Debugger',
                                reg_hive=winreg.HKEY_LOCAL_MACHINE,
                                reg_key=f'{self._IFEO_KEY}\\{sub_name}',
                                reg_value='Debugger', threat_type='registry',
                                reason=f'System process "{sub_name}" has a debugger interceptor set',
                                detail=f'Debugger: {dbg}\n'
                                       f'Every launch of {sub_name} runs the debugger first.',
                            ))
                        except OSError:
                            pass
                        winreg.CloseKey(sub_key)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass
        return out

    # ── hosts file scan ───────────────────────────────────────────────────────

    _MS_DOMAINS = (
        'windowsupdate.com', 'microsoft.com', 'windows.com',
        'office.com', 'live.com', 'microsoftonline.com',
        'smartscreen.microsoft.com', 'windowsdefender.com',
    )

    def _scan_hosts(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        hosts = r'C:\Windows\System32\drivers\etc\hosts'
        try:
            with open(hosts, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            bad: List[str] = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip, hostname = parts[0], parts[1].lower()
                if any(d in hostname for d in self._MS_DOMAINS):
                    if ip not in ('127.0.0.1', '0.0.0.0', '::1'):
                        bad.append(f'{hostname}  →  {ip}')
            if bad:
                out.append(ThreatEntry(
                    severity='red', category='Hosts File Hijack',
                    path=hosts, file_path=hosts, threat_type='file',
                    reason='Microsoft / Windows domains redirected to suspicious IPs',
                    detail='Redirected:\n' + '\n'.join(bad)
                           + '\n\nThis prevents Windows Update and Defender from working.',
                ))
        except (OSError, PermissionError):
            pass
        return out

    # ── scheduled tasks scan ──────────────────────────────────────────────────

    _TASK_SAFE_PATHS = (
        'system32', 'syswow64', 'microsoft', '\\windows\\',
        'program files', 'programfiles',
    )

    def _scan_tasks(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            result = subprocess.run(
                ['schtasks', '/query', '/fo', 'LIST', '/v'],
                capture_output=True, text=True, timeout=20,
                encoding='utf-8', errors='replace',
            )
            current = ''
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.lower().startswith('taskname:'):
                    current = line.split(':', 1)[1].strip()
                elif line.lower().startswith('task to run:'):
                    cmd = line.split(':', 1)[1].strip()
                    if not cmd or cmd == 'N/A':
                        continue
                    cl = cmd.lower()
                    if any(s in cl for s in self._TASK_SAFE_PATHS):
                        continue
                    if cl.startswith(self._temp) or cl.startswith(self._tmp):
                        out.append(ThreatEntry(
                            severity='red', category='Scheduled Task from Temp',
                            path=f'Task: {current}',
                            task_name=current, threat_type='task',
                            reason=f'Task launches executable from Temp: {cmd}',
                            detail=f'Task: {current}\nCommand: {cmd}',
                        ))
                    elif any(cl.endswith(e)
                             for e in ('.ps1', '.vbs', '.bat', '.cmd', '.hta')):
                        out.append(ThreatEntry(
                            severity='orange', category='Scheduled Script Task',
                            path=f'Task: {current}',
                            task_name=current, threat_type='task',
                            reason=f'Task runs a script at schedule: {cmd}',
                            detail=f'Task: {current}\nCommand: {cmd}',
                        ))
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return out

    # ── services scan ──────────────────────────────────────────────────────────

    def _scan_services(self) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r'SYSTEM\CurrentControlSet\Services')
            i = 0
            while True:
                try:
                    svc = winreg.EnumKey(key, i)
                    sk  = winreg.OpenKey(key, svc)
                    try:
                        img, _ = winreg.QueryValueEx(sk, 'ImagePath')
                        expanded = os.path.expandvars(img).lower()
                        if (expanded.startswith(self._temp) or
                                expanded.startswith(self._tmp)):
                            out.append(ThreatEntry(
                                severity='red', category='Service Binary in Temp',
                                path=f'Service: {svc}',
                                svc_name=svc, threat_type='service',
                                reason=f'Service "{svc}" binary is in a Temp directory',
                                detail=f'ImagePath: {img}',
                            ))
                    except OSError:
                        pass
                    winreg.CloseKey(sk)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass
        return out

    # ── blocking API ──────────────────────────────────────────────────────────

    def block_threat(self, threat: ThreatEntry) -> Tuple[bool, str]:
        """Dispatch to the appropriate blocking action and mark threat as blocked."""
        t = threat.threat_type
        if t == 'process':
            return self._kill_process(threat)
        if t == 'registry':
            return self._remove_registry_value(threat)
        if t == 'task':
            return self._disable_task(threat)
        if t == 'service':
            return self._disable_service(threat)
        return self._quarantine_file(threat)   # 'file' (default)

    def _quarantine_file(self, threat: ThreatEntry) -> Tuple[bool, str]:
        fpath = threat.file_path or threat.path
        if not os.path.isfile(fpath):
            return False, f'File not found: {fpath}'
        try:
            os.makedirs(QUARANTINE_DIR, exist_ok=True)
            with open(fpath, 'rb') as f:
                h = hashlib.md5(f.read(65536)).hexdigest()[:12]
            ts    = datetime.now().strftime('%Y%m%d_%H%M%S')
            qname = f'{ts}_{h}.quarantined'
            shutil.move(fpath, os.path.join(QUARANTINE_DIR, qname))
            manifest = self._load_manifest()
            manifest.append({
                'quarantine_name': qname,
                'original_path':   fpath,
                'original_name':   os.path.basename(fpath),
                'category':        threat.category,
                'severity':        threat.severity,
                'quarantined_at':  datetime.now().isoformat(),
            })
            self._save_manifest(manifest)
            threat.blocked = True
            return True, f'Quarantined: {os.path.basename(fpath)}'
        except PermissionError:
            return False, 'Access denied — run ST as Administrator'
        except Exception as exc:
            return False, str(exc)

    def _kill_process(self, threat: ThreatEntry) -> Tuple[bool, str]:
        try:
            import psutil
            if threat.pid:
                try:
                    psutil.Process(threat.pid).terminate()
                    threat.blocked = True
                    return True, f'Process PID {threat.pid} terminated'
                except psutil.NoSuchProcess:
                    threat.blocked = True
                    return True, 'Process already exited'
                except psutil.AccessDenied:
                    return False, 'Access denied — run ST as Administrator'
            # Fallback: match by exe path
            target = (threat.file_path or '').lower()
            killed = 0
            for p in psutil.process_iter(['pid', 'exe']):
                try:
                    if (p.info.get('exe') or '').lower() == target:
                        p.terminate()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if killed:
                threat.blocked = True
                return True, f'Terminated {killed} process(es)'
            return False, 'No matching process found'
        except ImportError:
            return False, 'psutil not installed'

    def _remove_registry_value(self, threat: ThreatEntry) -> Tuple[bool, str]:
        if not threat.reg_key or not threat.reg_value:
            return False, 'Missing registry key / value information'
        try:
            key = winreg.OpenKey(threat.reg_hive, threat.reg_key,
                                 access=winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, threat.reg_value)
            winreg.CloseKey(key)
            threat.blocked = True
            return True, f'Registry entry removed: {threat.reg_value}'
        except PermissionError:
            return False, 'Access denied — run ST as Administrator'
        except FileNotFoundError:
            threat.blocked = True
            return True, 'Registry entry no longer exists'
        except Exception as exc:
            return False, str(exc)

    def _disable_task(self, threat: ThreatEntry) -> Tuple[bool, str]:
        name = threat.task_name
        if not name:
            return False, 'No task name stored'
        try:
            r = subprocess.run(
                ['schtasks', '/change', '/tn', name, '/disable'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                threat.blocked = True
                return True, f'Task disabled: {name}'
            return False, r.stderr.strip() or f'schtasks exited {r.returncode}'
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, str(exc)

    def _disable_service(self, threat: ThreatEntry) -> Tuple[bool, str]:
        name = threat.svc_name
        if not name:
            return False, 'No service name stored'
        try:
            subprocess.run(['sc', 'stop', name],   capture_output=True, timeout=10)
            r = subprocess.run(
                ['sc', 'config', name, 'start=', 'disabled'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                threat.blocked = True
                return True, f'Service stopped and disabled: {name}'
            return False, r.stderr.strip() or f'sc exited {r.returncode}'
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, str(exc)

    # ── quarantine manifest ───────────────────────────────────────────────────

    def _load_manifest(self) -> list:
        try:
            with open(_MANIFEST, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _save_manifest(self, data: list) -> None:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        with open(_MANIFEST, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _fixed_drives(self) -> List[str]:
        drives: List[str] = []
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                dr = f'{chr(65 + i)}\\'
                if ctypes.windll.kernel32.GetDriveTypeW(dr) == 3:
                    drives.append(dr)
        return drives

    @staticmethod
    def _emit(cb, msg: str) -> None:
        if cb: cb(msg)

    @staticmethod
    def _emit_threat(cb, t: ThreatEntry) -> None:
        if cb: cb(t)
