"""Advanced Startup Manager backend.

Scans every known Windows auto-start location:
  Run / RunOnce registry keys (HKCU + HKLM, 32-bit + 64-bit + Policy)
  Winlogon hooks (Shell, Userinit, GinaDLL …)
  Boot Execute (Session Manager)
  AppInit_DLLs
  Startup folders (current user + All Users)
  Scheduled Tasks with Logon/Boot triggers
  Shell-extension context-menu handlers (right-click extensions)
  Browser Helper Objects (BHO)
  Image File Execution Options debugger hijacks (IFEO)
"""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

try:
    import winreg as _winreg
    _HAS_WINREG = True
except ImportError:
    _winreg = None           # type: ignore[assignment]
    _HAS_WINREG = False


class StartupEntry:
    __slots__ = ('name', 'command', 'location', 'location_type', 'enabled', 'note')

    def __init__(self, name: str, command: str, location: str,
                 location_type: str, enabled: bool = True, note: str = ''):
        self.name          = name
        self.command       = command
        self.location      = location
        self.location_type = location_type
        self.enabled       = enabled
        self.note          = note

    def detail_text(self) -> str:
        return (
            f"  Name      {self.name}\n"
            f"  Type      {self.location_type}\n"
            f"  Enabled   {'Yes' if self.enabled else 'No'}\n"
            f"  Location  {self.location}\n"
            f"\n"
            f"  Command:\n"
            f"  {self.command}\n"
            f"\n"
            f"  Note:\n"
            f"  {self.note or '(none)'}"
        )


# ── registry helpers ──────────────────────────────────────────────────────────

def _open_key(hive_str: str, subkey: str):
    if not _HAS_WINREG:
        return None
    hive = (_winreg.HKEY_CURRENT_USER
            if hive_str == 'HKCU' else _winreg.HKEY_LOCAL_MACHINE)
    try:
        return _winreg.OpenKey(
            hive, subkey, 0,
            _winreg.KEY_READ | _winreg.KEY_WOW64_64KEY,
        )
    except OSError:
        return None


def _enum_values(key) -> List[Tuple[str, str]]:
    out, i = [], 0
    while True:
        try:
            name, data, _ = _winreg.EnumValue(key, i)
            out.append((name, str(data) if data else ''))
            i += 1
        except OSError:
            break
    return out


def _clsid_name(clsid: str) -> str:
    """Resolve a CLSID GUID to a human-readable name (best-effort)."""
    clsid = clsid.strip()
    for hive in (_winreg.HKEY_LOCAL_MACHINE, _winreg.HKEY_CURRENT_USER):
        try:
            k = _winreg.OpenKey(
                hive, rf'SOFTWARE\Classes\CLSID\{clsid}',
                0, _winreg.KEY_READ | _winreg.KEY_WOW64_64KEY,
            )
            try:
                val, _ = _winreg.QueryValueEx(k, '')
                if val:
                    return f'{val}  ({clsid})'
            except OSError:
                pass
            finally:
                _winreg.CloseKey(k)
        except OSError:
            continue
    return clsid


# ── scanner ───────────────────────────────────────────────────────────────────

class StartupManager:
    _RUN_KEYS = [
        ('HKCU', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',        'HKCU Run'),
        ('HKCU', r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',    'HKCU RunOnce'),
        ('HKLM', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',        'HKLM Run'),
        ('HKLM', r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',    'HKLM RunOnce'),
        ('HKLM', r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run',     'HKLM Run (32-bit)'),
        ('HKLM', r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce', 'HKLM RunOnce (32-bit)'),
        ('HKLM', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run',  'HKLM Policy Run'),
        ('HKCU', r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run',  'HKCU Policy Run'),
    ]

    _SHELL_EXT_SUBKEYS = [
        r'SOFTWARE\Classes\*\shellex\ContextMenuHandlers',
        r'SOFTWARE\Classes\Directory\shellex\ContextMenuHandlers',
        r'SOFTWARE\Classes\Folder\shellex\ContextMenuHandlers',
        r'SOFTWARE\Classes\Directory\Background\shellex\ContextMenuHandlers',
        r'SOFTWARE\Classes\Drive\shellex\ContextMenuHandlers',
        r'SOFTWARE\Classes\AllFilesystemObjects\shellex\ContextMenuHandlers',
    ]

    _PS_TASKS = r"""
$startupClasses = 'MSFT_TaskLogonTrigger','MSFT_TaskBootTrigger','MSFT_TaskSessionStateChangeTrigger'
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' } | ForEach-Object {
    $t  = $_
    $ok = $t.Triggers | Where-Object { $startupClasses -contains $_.CimClass.CimClassName }
    if (-not $ok) { return }
    $a  = $t.Actions | Select-Object -First 1
    $tt = ($ok | Select-Object -First 1).CimClass.CimClassName
    [pscustomobject]@{
        N = $t.TaskName
        P = $t.TaskPath
        S = $t.State
        E = if ($a) { $a.Execute } else { '' }
        A = if ($a) { $a.Arguments } else { '' }
        T = $tt
    }
} | ConvertTo-Json -Compress -Depth 2
""".strip()

    def scan_all(self) -> List[StartupEntry]:
        scanners = [
            self._scan_run_keys,
            self._scan_winlogon,
            self._scan_boot_execute,
            self._scan_appinit_dlls,
            self._scan_startup_folders,
            self._scan_scheduled_tasks,
            self._scan_shell_extensions,
            self._scan_bho,
            self._scan_ifeo,
        ]
        results: List[StartupEntry] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(fn): fn.__name__ for fn in scanners}
            for fut in as_completed(futs):
                try:
                    results.extend(fut.result())
                except Exception:
                    pass
        return sorted(results, key=lambda e: (e.location_type, e.name.lower()))

    # ── individual scanners ───────────────────────────────────────────────────

    def _scan_run_keys(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        entries: List[StartupEntry] = []
        for hive_str, subkey, label in self._RUN_KEYS:
            key = _open_key(hive_str, subkey)
            if key is None:
                continue
            try:
                for name, cmd in _enum_values(key):
                    entries.append(StartupEntry(
                        name=name, command=cmd,
                        location=f'{hive_str}\\{subkey}',
                        location_type='Run Key',
                        enabled=True,
                        note=label,
                    ))
            finally:
                _winreg.CloseKey(key)
        return entries

    def _scan_winlogon(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        subkey = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
        key    = _open_key('HKLM', subkey)
        if key is None:
            return []
        entries: List[StartupEntry] = []
        try:
            for vname in ('Shell', 'Userinit', 'AppSetup', 'GinaDLL', 'UserInit'):
                try:
                    val, _ = _winreg.QueryValueEx(key, vname)
                    if val:
                        entries.append(StartupEntry(
                            name=vname, command=str(val),
                            location=f'HKLM\\{subkey}',
                            location_type='Winlogon',
                            enabled=True,
                            note='Loaded during Windows logon sequence',
                        ))
                except OSError:
                    pass
        finally:
            _winreg.CloseKey(key)
        return entries

    def _scan_boot_execute(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        subkey = r'SYSTEM\CurrentControlSet\Control\Session Manager'
        key    = _open_key('HKLM', subkey)
        if key is None:
            return []
        entries: List[StartupEntry] = []
        try:
            val, _ = _winreg.QueryValueEx(key, 'BootExecute')
            cmds   = val if isinstance(val, list) else [str(val)]
            for cmd in cmds:
                cmd = cmd.strip()
                if cmd:
                    entries.append(StartupEntry(
                        name='BootExecute', command=cmd,
                        location=f'HKLM\\{subkey}',
                        location_type='Boot Execute',
                        enabled=True,
                        note='Runs before Windows user-mode starts (very early boot)',
                    ))
        except OSError:
            pass
        finally:
            _winreg.CloseKey(key)
        return entries

    def _scan_appinit_dlls(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        entries: List[StartupEntry] = []
        for subkey in (
            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows',
            r'SOFTWARE\WOW6432Node\Microsoft\Windows NT\CurrentVersion\Windows',
        ):
            key = _open_key('HKLM', subkey)
            if key is None:
                continue
            try:
                val, _ = _winreg.QueryValueEx(key, 'AppInit_DLLs')
                if val:
                    entries.append(StartupEntry(
                        name='AppInit_DLLs', command=str(val),
                        location=f'HKLM\\{subkey}',
                        location_type='AppInit DLL',
                        enabled=True,
                        note='Injected into every user-mode process — high security risk',
                    ))
            except OSError:
                pass
            finally:
                _winreg.CloseKey(key)
        return entries

    def _scan_startup_folders(self) -> List[StartupEntry]:
        entries: List[StartupEntry] = []
        folders = [
            (os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup'),
             'Startup Folder (User)'),
            (os.path.expandvars(r'%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup'),
             'Startup Folder (All Users)'),
        ]
        for folder, label in folders:
            if not os.path.isdir(folder):
                continue
            try:
                for fname in os.listdir(folder):
                    fpath = os.path.join(folder, fname)
                    if not os.path.isfile(fpath):
                        continue
                    base, ext = os.path.splitext(fname)
                    enabled   = ext.lower() != '.disabled'
                    name      = base if not enabled else os.path.splitext(base)[0]
                    entries.append(StartupEntry(
                        name=name or fname,
                        command=fpath,
                        location=folder,
                        location_type='Startup Folder',
                        enabled=enabled,
                        note=label,
                    ))
            except OSError:
                pass
        return entries

    def _scan_scheduled_tasks(self) -> List[StartupEntry]:
        try:
            proc = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', self._PS_TASKS],
                capture_output=True, text=True, timeout=60,
            )
            raw = proc.stdout.strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            entries: List[StartupEntry] = []
            _trigger_labels = {
                'MSFT_TaskLogonTrigger':              'Logon trigger',
                'MSFT_TaskBootTrigger':               'Boot trigger',
                'MSFT_TaskSessionStateChangeTrigger': 'Session trigger',
            }
            for t in data:
                name  = (t.get('N') or '').strip()
                path  = (t.get('P') or '\\').strip()
                state = (t.get('S') or '').strip()
                exe   = (t.get('E') or '').strip().strip('"')
                args  = (t.get('A') or '').strip()
                ttype = (t.get('T') or '').strip()
                if not name:
                    continue
                cmd     = f'{exe} {args}'.strip()
                enabled = state in ('Ready', 'Running')
                entries.append(StartupEntry(
                    name=name,
                    command=cmd or '(no action)',
                    location=f'Task Scheduler  {path}',
                    location_type='Scheduled Task',
                    enabled=enabled,
                    note=_trigger_labels.get(ttype, ttype) + f'  |  State: {state}',
                ))
            return entries
        except Exception:
            return []

    def _scan_shell_extensions(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        entries: List[StartupEntry] = []
        seen: set = set()
        for subkey in self._SHELL_EXT_SUBKEYS:
            for hive_str in ('HKLM', 'HKCU'):
                key = _open_key(hive_str, subkey)
                if key is None:
                    continue
                try:
                    idx = 0
                    while True:
                        try:
                            handler_name = _winreg.EnumKey(key, idx)
                            idx += 1
                            hk = _winreg.OpenKey(
                                key, handler_name, 0,
                                _winreg.KEY_READ | _winreg.KEY_WOW64_64KEY,
                            )
                            try:
                                clsid, _ = _winreg.QueryValueEx(hk, '')
                            except OSError:
                                clsid = handler_name
                            finally:
                                _winreg.CloseKey(hk)
                            clsid_s = str(clsid).strip()
                            uid     = f'{hive_str}|{subkey}|{handler_name}'
                            if uid in seen:
                                continue
                            seen.add(uid)
                            friendly = _clsid_name(clsid_s) if _HAS_WINREG else clsid_s
                            # An empty CLSID value means the extension is disabled
                            enabled = bool(clsid_s)
                            entries.append(StartupEntry(
                                name=handler_name,
                                command=clsid_s,
                                location=f'{hive_str}\\{subkey}',
                                location_type='Shell Extension',
                                enabled=enabled,
                                note=friendly,
                            ))
                        except OSError:
                            break
                finally:
                    _winreg.CloseKey(key)
        return entries

    def _scan_bho(self) -> List[StartupEntry]:
        if not _HAS_WINREG:
            return []
        subkey = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Browser Helper Objects'
        key    = _open_key('HKLM', subkey)
        if key is None:
            return []
        entries: List[StartupEntry] = []
        try:
            idx = 0
            while True:
                try:
                    clsid = _winreg.EnumKey(key, idx)
                    idx  += 1
                    name  = _clsid_name(clsid)
                    entries.append(StartupEntry(
                        name=name,
                        command=clsid,
                        location=f'HKLM\\{subkey}',
                        location_type='Browser Helper Object',
                        enabled=True,
                        note='IE/legacy browser extension (BHO)',
                    ))
                except OSError:
                    break
        finally:
            _winreg.CloseKey(key)
        return entries

    def _scan_ifeo(self) -> List[StartupEntry]:
        """Image File Execution Options — debugger hijacks."""
        if not _HAS_WINREG:
            return []
        subkey = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
        key    = _open_key('HKLM', subkey)
        if key is None:
            return []
        entries: List[StartupEntry] = []
        try:
            idx = 0
            while True:
                try:
                    app_name = _winreg.EnumKey(key, idx)
                    idx     += 1
                    app_key  = _winreg.OpenKey(
                        key, app_name, 0,
                        _winreg.KEY_READ | _winreg.KEY_WOW64_64KEY,
                    )
                    try:
                        dbg, _ = _winreg.QueryValueEx(app_key, 'Debugger')
                        if dbg:
                            entries.append(StartupEntry(
                                name=app_name,
                                command=str(dbg),
                                location=f'HKLM\\{subkey}\\{app_name}',
                                location_type='IFEO Debugger',
                                enabled=True,
                                note='Intercepts / replaces execution of this EXE (can indicate rootkit)',
                            ))
                    except OSError:
                        pass
                    finally:
                        _winreg.CloseKey(app_key)
                except OSError:
                    break
        finally:
            _winreg.CloseKey(key)
        return entries

    # ── enable / disable ──────────────────────────────────────────────────────

    def disable_entry(self, entry: StartupEntry) -> Tuple[bool, str]:
        if entry.location_type == 'Run Key':
            return self._toggle_run_key(entry, enable=False)
        if entry.location_type == 'Startup Folder':
            return self._toggle_startup_file(entry, enable=False)
        return False, f"Cannot disable entries of type '{entry.location_type}'"

    def enable_entry(self, entry: StartupEntry) -> Tuple[bool, str]:
        if entry.location_type == 'Run Key':
            return self._toggle_run_key(entry, enable=True)
        if entry.location_type == 'Startup Folder':
            return self._toggle_startup_file(entry, enable=True)
        return False, f"Cannot enable entries of type '{entry.location_type}'"

    def _toggle_run_key(self, entry: StartupEntry, enable: bool) -> Tuple[bool, str]:
        """Use Windows StartupApproved to enable/disable Run key entries."""
        if not _HAS_WINREG:
            return False, 'winreg not available'
        loc        = entry.location
        is_hkcu    = loc.startswith('HKCU')
        is_32bit   = 'WOW6432Node' in loc
        hive       = _winreg.HKEY_CURRENT_USER if is_hkcu else _winreg.HKEY_LOCAL_MACHINE
        aprv_sub   = (
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32'
            if is_32bit else
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
        )
        try:
            try:
                aprv_key = _winreg.OpenKey(
                    hive, aprv_sub, 0,
                    _winreg.KEY_READ | _winreg.KEY_WRITE | _winreg.KEY_WOW64_64KEY,
                )
            except OSError:
                aprv_key = _winreg.CreateKeyEx(
                    hive, aprv_sub, 0,
                    _winreg.KEY_READ | _winreg.KEY_WRITE | _winreg.KEY_WOW64_64KEY,
                )
            try:
                # First byte 02 = enabled, 03 = disabled; rest are timestamp zeros
                data = (b'\x02' if enable else b'\x03') + b'\x00' * 11
                _winreg.SetValueEx(aprv_key, entry.name, 0, _winreg.REG_BINARY, data)
                return True, ''
            finally:
                _winreg.CloseKey(aprv_key)
        except OSError as exc:
            return False, str(exc)

    @staticmethod
    def _toggle_startup_file(entry: StartupEntry, enable: bool) -> Tuple[bool, str]:
        orig     = entry.command
        disabled = orig + '.disabled'
        try:
            if enable:
                src, dst = disabled, orig
            else:
                src, dst = orig, disabled
            if not os.path.exists(src):
                return False, f'File not found: {src}'
            os.rename(src, dst)
            return True, ''
        except OSError as exc:
            return False, str(exc)
