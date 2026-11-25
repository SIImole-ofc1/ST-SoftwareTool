"""Operating-system checks and blocking actions for ST-AntiVirus.

`system_checks(files)` returns a list of (progress message, check function) stages for the
current OS. Each check returns ThreatEntry findings and adds every program it discovers
(running processes, autostart entries, services, ...) to `files`, so the scanner can
analyse those files with the detection engines.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import Callable, List, Tuple

from core.av_engine import DATA_DIR, IS_WINDOWS, is_signed, security_center_products
from core.av_types import ThreatEntry, file_threat, norm

if IS_WINDOWS:
    import winreg

QUARANTINE_DIR = os.path.join(DATA_DIR, 'quarantine')
_MANIFEST      = os.path.join(QUARANTINE_DIR, 'manifest.json')

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

FAKE_SYSNAMES = {
    'svchost32.exe', 'svhost.exe',    'explorer32.exe', 'winlogon32.exe',
    'csrss32.exe',   'lsass32.exe',   'spoolsv32.exe',  'taskhost32.exe',
    'services32.exe','userinit32.exe','rundll.exe',      'svchst.exe',
    'lsas.exe',      'isass.exe',
}

_SCRIPT_TARGETS = ('.ps1', '.vbs', '.bat', '.cmd', '.hta')


def temp_dirs() -> Tuple[str, ...]:
    dirs = {norm(tempfile.gettempdir())}
    if IS_WINDOWS:
        for var in ('TEMP', 'TMP'):
            if os.environ.get(var):
                dirs.add(norm(os.environ[var]))
    else:
        dirs.update({norm('/tmp'), norm('/var/tmp'), norm('/dev/shm')})
    return tuple(sorted(dirs))


def exe_from_cmd(cmd: str) -> str:
    """Extract the program path from a command line / registry value."""
    cmd = os.path.expandvars((cmd or '').strip())
    if not cmd:
        return ''
    if cmd[0] in '"\'':
        end = cmd.find(cmd[0], 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    low = cmd.lower()
    for ext in ('.exe', '.com', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.scr', '.dll'):
        i = low.find(ext)
        if i > 0:
            return cmd[:i + len(ext)]
    return cmd.split(' ')[0]


# ══ checks shared by every OS ═════════════════════════════════════════════════

def _scan_processes(files: set) -> List[ThreatEntry]:
    out: List[ThreatEntry] = []
    temps = temp_dirs()
    try:
        import psutil
    except ImportError:
        return out
    for proc in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            exe  = proc.info.get('exe') or ''
            name = (proc.info.get('name') or '').lower()
            pid  = proc.info.get('pid', 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not exe:
            continue
        files.add(exe)
        if norm(exe).startswith(temps) and not is_signed(exe):
            out.append(ThreatEntry(
                severity='orange', category='Unsigned Process Running from Temp',
                path=f'{name}  (PID {pid})', file_path=exe, pid=pid, threat_type='process',
                reason=f'"{name}" (PID {pid}) is executing from a Temp directory',
                detail=f'Path: {exe}\nMalware often runs from temporary folders. Installers do '
                       'too — kill it if you are not installing anything right now.'))
        elif IS_WINDOWS and name in FAKE_SYSNAMES:
            out.append(ThreatEntry(
                severity='red', category='Fake System Process Running',
                path=f'{name}  (PID {pid})', file_path=exe, pid=pid, threat_type='process',
                reason=f'Running process mimics a Windows system process: {name}',
                detail=f'PID {pid} — path: {exe}'))
    return out


_MS_DOMAINS = (
    'windowsupdate.com', 'microsoft.com', 'windows.com', 'office.com', 'live.com',
    'microsoftonline.com', 'smartscreen.microsoft.com', 'windowsdefender.com',
    'apple.com', 'icloud.com', 'ubuntu.com', 'debian.org',
)


def _scan_hosts(files: set) -> List[ThreatEntry]:
    hosts = (os.path.expandvars(r'%WINDIR%\System32\drivers\etc\hosts')
             if IS_WINDOWS else '/etc/hosts')
    bad: List[str] = []
    try:
        with open(hosts, encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split('#', 1)[0].split()
                if len(parts) < 2:
                    continue
                ip = parts[0]
                for hostname in parts[1:]:
                    if any(d in hostname.lower() for d in _MS_DOMAINS) and \
                            ip not in ('127.0.0.1', '0.0.0.0', '::1'):
                        bad.append(f'{hostname}  →  {ip}')
    except OSError:
        return []
    if not bad:
        return []
    return [ThreatEntry(
        severity='red', category='Hosts File Hijack', path=hosts, file_path=hosts,
        threat_type='info',
        reason='System / update domains are redirected to other servers',
        detail='Redirected:\n' + '\n'.join(bad) +
               '\n\nThis can block security updates or send you to fake sites. '
               'Remove these lines from the hosts file.')]


# ══ Windows ═══════════════════════════════════════════════════════════════════

def _check_protection(files: set) -> List[ThreatEntry]:
    products = security_center_products()
    if not products:
        return []
    if any(on for _, on, _ in products):
        stale = [n for n, on, fresh in products if on and not fresh]
        if stale:
            return [ThreatEntry(
                severity='orange', category='Outdated Virus Definitions',
                path='Windows Security Center', threat_type='info',
                reason=f'{", ".join(stale)} reports out-of-date virus definitions',
                detail='Open your antivirus and run an update so it recognises new threats.')]
        return []
    return [ThreatEntry(
        severity='red', category='Real-Time Protection Off',
        path='Windows Security Center', threat_type='info',
        reason='No antivirus on this PC has real-time protection turned on',
        detail='Registered products: ' + ', '.join(n for n, _, _ in products) +
               '\n\nOpen Windows Security > Virus & threat protection and turn on '
               'Real-time protection.')]


_RUN_KEYS = [
    ('HKCU', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
    ('HKLM', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
    ('HKCU', r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
    ('HKLM', r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
    ('HKLM', r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'),
]
_WINLOGON_KEY = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
_IFEO_KEY     = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
_IFEO_TARGETS = {
    'svchost.exe', 'explorer.exe', 'lsass.exe', 'winlogon.exe', 'csrss.exe', 'services.exe',
    'smss.exe', 'wininit.exe', 'taskmgr.exe', 'regedit.exe', 'cmd.exe', 'powershell.exe',
}


def _hive(name: str):
    return winreg.HKEY_CURRENT_USER if name == 'HKCU' else winreg.HKEY_LOCAL_MACHINE


def _scan_registry(files: set) -> List[ThreatEntry]:
    out: List[ThreatEntry] = []
    temps = temp_dirs()
    for hname, key_path in _RUN_KEYS:
        hive = _hive(hname)
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                vname, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if not isinstance(value, str):
                continue
            target = exe_from_cmd(value)
            if target:
                files.add(target)
            tl = norm(target)
            common = dict(path=f'{hname}\\{key_path}  →  {vname}', reg_hive=hive,
                          reg_key=key_path, reg_value=vname, threat_type='registry',
                          detail=f'Value: {value}')
            if tl.startswith(temps):
                out.append(ThreatEntry(severity='red', category='Startup Entry from Temp',
                                       reason='Startup entry runs a program from a Temp '
                                              'directory at every logon', **common))
            elif tl.endswith(_SCRIPT_TARGETS):
                out.append(ThreatEntry(severity='orange', category='Script Startup Entry',
                                       reason='Startup entry runs a script at every logon',
                                       **common))
        winreg.CloseKey(key)

    # Winlogon Shell / Userinit hijack
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WINLOGON_KEY)
        for vname, expected in (('Shell', 'explorer.exe'), ('Userinit', 'userinit.exe')):
            try:
                value, _ = winreg.QueryValueEx(key, vname)
            except OSError:
                continue
            if expected not in str(value).lower():
                out.append(ThreatEntry(
                    severity='red', category='Winlogon Hijack',
                    path=f'HKLM\\{_WINLOGON_KEY}  →  {vname}',
                    reg_hive=winreg.HKEY_LOCAL_MACHINE, reg_key=_WINLOGON_KEY,
                    reg_value=vname, threat_type='registry',
                    reason=f'Winlogon "{vname}" is modified: {value}',
                    detail=f'Expected "{expected}", found "{value}".\n'
                           'Classic malware persistence — runs at every logon.'))
        winreg.CloseKey(key)
    except OSError:
        pass

    # Image File Execution Options debugger hijack
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _IFEO_KEY)
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if sub_name.lower() not in _IFEO_TARGETS:
                continue
            try:
                with winreg.OpenKey(key, sub_name) as sub_key:
                    dbg, _ = winreg.QueryValueEx(sub_key, 'Debugger')
            except OSError:
                continue
            out.append(ThreatEntry(
                severity='red', category='IFEO Debugger Hijack',
                path=f'HKLM\\{_IFEO_KEY}\\{sub_name}  →  Debugger',
                reg_hive=winreg.HKEY_LOCAL_MACHINE, reg_key=f'{_IFEO_KEY}\\{sub_name}',
                reg_value='Debugger', threat_type='registry',
                reason=f'System program "{sub_name}" has a debugger interceptor set',
                detail=f'Debugger: {dbg}\nEvery launch of {sub_name} runs this program first.'))
        winreg.CloseKey(key)
    except OSError:
        pass
    return out


_TASK_SAFE_PATHS = ('system32', 'syswow64', '\\windows\\', 'program files', 'programfiles')


def _scan_tasks(files: set) -> List[ThreatEntry]:
    out: List[ThreatEntry] = []
    temps = temp_dirs()
    try:
        result = subprocess.run(['schtasks', '/query', '/fo', 'LIST', '/v'],
                                capture_output=True, text=True, timeout=30,
                                encoding='utf-8', errors='replace', creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError):
        return out
    current, seen = '', set()
    for line in result.stdout.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith('taskname:'):
            current = line.split(':', 1)[1].strip()
        elif low.startswith('task to run:'):
            cmd = line.split(':', 1)[1].strip()
            if not cmd or cmd == 'N/A' or (current, cmd) in seen:
                continue
            seen.add((current, cmd))
            target = exe_from_cmd(cmd)
            if target and os.path.isabs(target):
                files.add(target)
            cl = norm(target) if target else cmd.lower()
            if any(s in cl for s in _TASK_SAFE_PATHS):
                continue
            common = dict(path=f'Task: {current}', task_name=current, threat_type='task',
                          detail=f'Task: {current}\nCommand: {cmd}')
            if cl.startswith(temps):
                out.append(ThreatEntry(severity='red', category='Scheduled Task from Temp',
                                       reason=f'Task launches a program from Temp: {cmd}',
                                       **common))
            elif cl.endswith(_SCRIPT_TARGETS):
                out.append(ThreatEntry(severity='orange', category='Scheduled Script Task',
                                       reason=f'Task runs a script on a schedule: {cmd}',
                                       **common))
    return out


def _scan_services(files: set) -> List[ThreatEntry]:
    out: List[ThreatEntry] = []
    temps = temp_dirs()
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services')
    except OSError:
        return out
    i = 0
    while True:
        try:
            svc = winreg.EnumKey(key, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(key, svc) as sk:
                img, _ = winreg.QueryValueEx(sk, 'ImagePath')
        except OSError:
            continue
        target = exe_from_cmd(str(img))
        if target.lower().startswith('\\systemroot\\'):
            target = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), target[12:])
        if target.startswith('\\??\\'):
            target = target[4:]
        if os.path.isabs(target):
            files.add(target)
        if norm(target).startswith(temps):
            out.append(ThreatEntry(
                severity='red', category='Service Binary in Temp', path=f'Service: {svc}',
                svc_name=svc, threat_type='service',
                reason=f'Service "{svc}" runs a program from a Temp directory',
                detail=f'ImagePath: {img}'))
    winreg.CloseKey(key)
    return out


# ══ macOS / Linux autostart locations ═════════════════════════════════════════

def _scan_posix_autostart(files: set) -> List[ThreatEntry]:
    out: List[ThreatEntry] = []
    temps = temp_dirs()
    found: List[Tuple[str, str]] = []          # (where, program)
    home = os.path.expanduser('~')
    if sys.platform == 'darwin':
        for d in (f'{home}/Library/LaunchAgents', '/Library/LaunchAgents', '/Library/LaunchDaemons'):
            for plist in glob.glob(os.path.join(d, '*.plist')):
                try:
                    with open(plist, 'rb') as f:
                        data = plistlib.load(f)
                except Exception:
                    continue
                prog = data.get('Program') or (data.get('ProgramArguments') or [''])[0]
                if prog:
                    found.append((plist, prog))
    else:
        for d in (f'{home}/.config/autostart', '/etc/xdg/autostart'):
            for desktop in glob.glob(os.path.join(d, '*.desktop')):
                try:
                    with open(desktop, encoding='utf-8', errors='replace') as f:
                        for line in f:
                            if line.startswith('Exec='):
                                found.append((desktop, exe_from_cmd(line[5:])))
                                break
                except OSError:
                    continue
    for where, prog in found:
        if os.path.isabs(prog):
            files.add(prog)
        if norm(prog).startswith(temps):
            out.append(file_threat(
                'red', 'Autostart Entry from Temp', where,
                f'Program started at login lives in a temporary folder: {prog}',
                'Malware commonly persists by launching from /tmp at every login.'))
    return out


def system_checks() -> List[Tuple[str, Callable[[set], List[ThreatEntry]]]]:
    """Ordered (progress message, check) stages for this OS."""
    if IS_WINDOWS:
        return [
            ('Checking Windows protection status…', _check_protection),
            ('Scanning running processes…',         _scan_processes),
            ('Scanning registry startup entries…',  _scan_registry),
            ('Checking hosts file…',                _scan_hosts),
            ('Scanning scheduled tasks…',           _scan_tasks),
            ('Checking Windows services…',          _scan_services),
        ]
    return [
        ('Scanning running processes…',  _scan_processes),
        ('Scanning autostart entries…',  _scan_posix_autostart),
        ('Checking hosts file…',         _scan_hosts),
    ]


# ══ blocking actions ══════════════════════════════════════════════════════════

def block_threat(threat: ThreatEntry) -> Tuple[bool, str]:
    """Quarantine / kill / remove the threat. Marks it blocked on success."""
    t = threat.threat_type
    if t == 'info':
        return False, 'This is a status warning — there is nothing to quarantine.'
    if t == 'process':
        return _kill_process(threat)
    if t in ('registry', 'task', 'service') and not IS_WINDOWS:
        return False, 'Not supported on this operating system.'
    if t == 'registry':
        return _remove_registry_value(threat)
    if t == 'task':
        return _disable_task(threat)
    if t == 'service':
        return _disable_service(threat)
    return _quarantine_file(threat)


def _denied() -> str:
    return ('Access denied — run ST as Administrator' if IS_WINDOWS
            else 'Permission denied — run ST with sudo')


def _quarantine_file(threat: ThreatEntry) -> Tuple[bool, str]:
    fpath = threat.file_path or threat.path
    if not os.path.isfile(fpath):
        return False, f'File not found: {fpath}'
    try:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        with open(fpath, 'rb') as f:
            h = hashlib.md5(f.read(65536)).hexdigest()[:12]
        qname = f'{datetime.now():%Y%m%d_%H%M%S}_{h}.quarantined'
        shutil.move(fpath, os.path.join(QUARANTINE_DIR, qname))
        try:
            os.chmod(os.path.join(QUARANTINE_DIR, qname), 0o600)   # never executable
        except OSError:
            pass
        manifest = _load_manifest()
        manifest.append({
            'quarantine_name': qname,
            'original_path':   fpath,
            'original_name':   os.path.basename(fpath),
            'category':        threat.category,
            'severity':        threat.severity,
            'quarantined_at':  datetime.now().isoformat(),
        })
        _save_manifest(manifest)
        threat.blocked = True
        return True, f'Quarantined: {os.path.basename(fpath)}'
    except PermissionError:
        return False, _denied()
    except Exception as exc:
        return False, str(exc)


def _kill_process(threat: ThreatEntry) -> Tuple[bool, str]:
    try:
        import psutil
    except ImportError:
        return False, 'psutil not installed'
    if threat.pid:
        try:
            psutil.Process(threat.pid).terminate()
            threat.blocked = True
            return True, f'Process PID {threat.pid} terminated'
        except psutil.NoSuchProcess:
            threat.blocked = True
            return True, 'Process already exited'
        except psutil.AccessDenied:
            return False, _denied()
    target = norm(threat.file_path)
    killed = 0
    for p in psutil.process_iter(['pid', 'exe']):
        try:
            if norm(p.info.get('exe') or '') == target:
                p.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        threat.blocked = True
        return True, f'Terminated {killed} process(es)'
    return False, 'No matching process found'


def _remove_registry_value(threat: ThreatEntry) -> Tuple[bool, str]:
    if not threat.reg_key or not threat.reg_value:
        return False, 'Missing registry key / value information'
    try:
        key = winreg.OpenKey(threat.reg_hive, threat.reg_key, access=winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, threat.reg_value)
        winreg.CloseKey(key)
        threat.blocked = True
        return True, f'Registry entry removed: {threat.reg_value}'
    except PermissionError:
        return False, _denied()
    except FileNotFoundError:
        threat.blocked = True
        return True, 'Registry entry no longer exists'
    except Exception as exc:
        return False, str(exc)


def _disable_task(threat: ThreatEntry) -> Tuple[bool, str]:
    if not threat.task_name:
        return False, 'No task name stored'
    try:
        r = subprocess.run(['schtasks', '/change', '/tn', threat.task_name, '/disable'],
                           capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if r.returncode == 0:
        threat.blocked = True
        return True, f'Task disabled: {threat.task_name}'
    return False, r.stderr.strip() or f'schtasks exited {r.returncode}'


def _disable_service(threat: ThreatEntry) -> Tuple[bool, str]:
    if not threat.svc_name:
        return False, 'No service name stored'
    try:
        subprocess.run(['sc', 'stop', threat.svc_name], capture_output=True, timeout=10,
                       creationflags=_NO_WINDOW)
        r = subprocess.run(['sc', 'config', threat.svc_name, 'start=', 'disabled'],
                           capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if r.returncode == 0:
        threat.blocked = True
        return True, f'Service stopped and disabled: {threat.svc_name}'
    return False, r.stderr.strip() or f'sc exited {r.returncode}'


def _load_manifest() -> list:
    try:
        with open(_MANIFEST, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_manifest(data: list) -> None:
    os.makedirs(QUARANTINE_DIR, exist_ok=True)
    with open(_MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
