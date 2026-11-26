"""Advanced Startup Manager backend for macOS.

Scans the places where programs start automatically on a Mac:
  Login Items              "Open at Login" apps (System Settings › General › Login Items)
  Launch Agents            ~/Library/LaunchAgents      (your account)
  Launch Agents (all users) /Library/LaunchAgents      (every account, run as that user)
  Launch Daemons           /Library/LaunchDaemons      (run as root when the Mac starts)
  Cron Jobs                your crontab
  Kernel Extensions        /Library/Extensions         (read-only)
  System Extensions        systemextensionsctl         (read-only)

Apple's own items in /System are left out: System Integrity Protection blocks changes.
Launch Agents/Daemons are switched with `launchctl enable/disable` (the macOS override list,
the plist file is never edited); Launch Daemons need an administrator password.
Login Items are switched by removing / re-adding them; removed ones are remembered so they
stay in the list and can be turned back on.
"""
from __future__ import annotations

import glob
import json
import os
import plistlib
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple

from core.mac_admin import CANCELLED, applescript_quote, run_as_admin
from core.startup_manager import StartupEntry

# launchd labels we are willing to pass to launchctl (and, for daemons, to a root shell)
_LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,200}')

_DATA_DIR = os.path.expanduser('~/Library/Application Support/ST-SoftwareTool')
_DISABLED_LOGIN_ITEMS = os.path.join(_DATA_DIR, 'disabled_login_items.json')

_LAUNCHD_LOCATIONS = (
    ('~/Library/LaunchAgents', 'Launch Agent',             'gui'),
    ('/Library/LaunchAgents',  'Launch Agent (all users)', 'gui'),
    ('/Library/LaunchDaemons', 'Launch Daemon',            'system'),
)

_LIST_LOGIN_ITEMS = '''
set out to ""
tell application "System Events"
    repeat with li in login items
        set n to ""
        set p to ""
        set h to false
        try
            set n to name of li
        end try
        try
            set p to path of li
            if p is missing value then set p to ""
        end try
        try
            set h to hidden of li
        end try
        set out to out & n & tab & p & tab & (h as text) & linefeed
    end repeat
end tell
return out
'''


def _run(args: List[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          errors='replace')


def _osascript(script: str, timeout: float = 30) -> Tuple[bool, str]:
    try:
        r = _run(['osascript', '-e', script], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 'macOS did not answer in time (a permission prompt may be waiting).'
    except OSError as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, r.stdout
    err = (r.stderr or '').strip()
    if '-1743' in err or 'Not authorized' in err:
        return False, ('ST-SoftwareTool is not allowed to read Login Items. Allow it in '
                       'System Settings › Privacy & Security › Automation › System Events.')
    return False, err or f'osascript exit code {r.returncode}'


def _gui_domain() -> str:
    return f'gui/{os.getuid()}'


def _disabled_overrides(domain: str) -> Dict[str, bool]:
    """launchd's own enable/disable list for a domain: {label: is_disabled}."""
    try:
        out = _run(['launchctl', 'print-disabled', domain]).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return {m.group(1): m.group(2) in ('disabled', 'true')
            for m in re.finditer(r'"([^"]+)"\s*=>\s*(\w+)', out)}


def _fmt_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f'{seconds // 3600} h'
    if seconds % 60 == 0:
        return f'{seconds // 60} min'
    return f'{seconds} s'


def _load_disabled_login_items() -> List[dict]:
    try:
        with open(_DISABLED_LOGIN_ITEMS, encoding='utf-8') as f:
            items = json.load(f)
        return [i for i in items if isinstance(i, dict) and i.get('name') and i.get('path')]
    except (OSError, ValueError):
        return []


def _save_disabled_login_items(items: List[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _DISABLED_LOGIN_ITEMS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, _DISABLED_LOGIN_ITEMS)


class MacStartupManager:
    TYPE_COLORS = {
        'Login Item':               '#4488ff',   # Open at Login — blue
        'Launch Agent':             '#44ff88',   # your account — light green
        'Launch Agent (all users)': '#00cc44',   # every account — green
        'Launch Daemon':            '#ffaa00',   # runs as root — amber
        'Cron Job':                 '#ff8800',   # scheduled command — orange
        'System Extension':         '#aaaaaa',   # driver / network / security ext. — grey
        'Kernel Extension':         '#ff4444',   # runs inside the kernel — red
    }
    TOGGLE_TYPES = ('Login Item', 'Launch Agent', 'Launch Agent (all users)', 'Launch Daemon')
    TOGGLE_NOTE  = ('Enable / Disable works for Login Items, Launch Agents and Launch Daemons '
                    '(Daemons ask for your password).  Changes apply from the next login.')

    def __init__(self):
        self.warnings: List[str] = []   # problems from the last scan, shown by the UI

    # ── scanning ──────────────────────────────────────────────────────────────

    def scan_all(self) -> List[StartupEntry]:
        self.warnings = []
        scanners = [self._scan_login_items, self._scan_cron, self._scan_kernel_extensions,
                    self._scan_system_extensions]
        scanners += [lambda loc=loc: self._scan_launchd(*loc) for loc in _LAUNCHD_LOCATIONS]
        results: List[StartupEntry] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for fut in [pool.submit(fn) for fn in scanners]:
                try:
                    results.extend(fut.result())
                except Exception as exc:
                    self.warnings.append(str(exc))
        return sorted(results, key=lambda e: (e.location_type, e.name.lower()))

    def _scan_launchd(self, folder: str, kind: str, domain_kind: str) -> List[StartupEntry]:
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            return []
        domain = _gui_domain() if domain_kind == 'gui' else 'system'
        overrides = _disabled_overrides(domain)
        entries = []
        for path in sorted(glob.glob(os.path.join(folder, '*.plist'))):
            stem = os.path.basename(path)[:-len('.plist')]
            try:
                with open(path, 'rb') as f:
                    d = plistlib.load(f)
                if not isinstance(d, dict):
                    raise ValueError('not a dictionary')
            except Exception:
                entries.append(StartupEntry(stem, '', path, kind, True,
                                            'Could not read this file (no permission or damaged).'))
                continue
            label = str(d.get('Label') or stem)
            args = d.get('ProgramArguments')
            args = [str(a) for a in args] if isinstance(args, list) else []
            program = str(d.get('Program') or (args[0] if args else ''))
            command = shlex.join(args) if args else program

            if label in overrides:
                enabled = not overrides[label]
            else:
                enabled = not bool(d.get('Disabled'))

            entries.append(StartupEntry(
                label, command, path, kind, enabled,
                self._launchd_note(d, program, domain_kind, kind),
                extra={'label': label, 'domain': domain_kind},
            ))
        return entries

    @staticmethod
    def _launchd_note(d: dict, program: str, domain_kind: str, kind: str) -> str:
        notes = []
        triggers = False
        if d.get('RunAtLoad'):
            notes.append('Starts when the Mac starts' if domain_kind == 'system'
                         else 'Starts at login')
            triggers = True
        if d.get('KeepAlive'):
            notes.append('Restarted automatically if it quits')
            triggers = True
        interval = d.get('StartInterval')
        if isinstance(interval, int) and interval > 0:
            notes.append(f'Runs every {_fmt_interval(interval)}')
            triggers = True
        if d.get('StartCalendarInterval'):
            notes.append('Runs on a schedule')
            triggers = True
        if d.get('WatchPaths') or d.get('QueueDirectories'):
            notes.append('Runs when certain files change')
            triggers = True
        if not triggers:
            notes.append('Starts only when another program asks for it')
        if domain_kind == 'system':
            notes.append(f"Runs as {d['UserName']}" if d.get('UserName')
                         else 'Runs as root (full administrator rights)')
        if kind == 'Launch Agent (all users)':
            notes.append('Enable / Disable here affects only your account')
        if program and os.path.isabs(program) and not os.path.exists(program):
            notes.append('Program file not found — probably left over from an uninstalled app')
        return '. '.join(notes) + '.'

    def _scan_login_items(self) -> List[StartupEntry]:
        ok, out = _osascript(_LIST_LOGIN_ITEMS)
        entries, active_paths = [], set()
        if ok:
            for line in out.splitlines():
                parts = line.split('\t')
                if len(parts) < 3 or not parts[0]:
                    continue
                name, path, hidden = parts[0], parts[1], parts[2].strip() == 'true'
                active_paths.add(path)
                entries.append(StartupEntry(
                    name, path, 'System Settings › General › Login Items', 'Login Item', True,
                    'Opens at login' + (' (hidden)' if hidden else '') + '.',
                    extra={'path': path, 'hidden': hidden},
                ))
        else:
            self.warnings.append(out)
        for item in _load_disabled_login_items():
            if item['path'] in active_paths:
                continue   # turned back on outside the app
            entries.append(StartupEntry(
                item['name'], item['path'], 'Turned off with ST-SoftwareTool', 'Login Item', False,
                'Was removed from Login Items by ST-SoftwareTool. Enable to add it back.',
                extra={'path': item['path'], 'hidden': bool(item.get('hidden'))},
            ))
        return entries

    @staticmethod
    def _scan_cron() -> List[StartupEntry]:
        try:
            r = _run(['crontab', '-l'])
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            return []   # "no crontab for <user>"
        entries = []
        for line in r.stdout.splitlines():
            s = line.strip()
            if not s or s.startswith('#') or re.match(r'[A-Za-z_][A-Za-z0-9_]*\s*=', s):
                continue
            if s.startswith('@'):
                schedule, _, command = s.partition(' ')
            else:
                fields = s.split(None, 5)
                if len(fields) < 6:
                    continue
                schedule, command = ' '.join(fields[:5]), fields[5]
            try:
                name = os.path.basename(shlex.split(command)[0])
            except (ValueError, IndexError):
                name = command[:40]
            note = ('Runs every time the Mac starts.' if schedule == '@reboot'
                    else f'Schedule: {schedule}. Change it with "crontab -e" in Terminal.')
            entries.append(StartupEntry(name or command[:40], command, 'crontab (your account)',
                                        'Cron Job', True, note))
        return entries

    @staticmethod
    def _scan_kernel_extensions() -> List[StartupEntry]:
        entries = []
        for path in sorted(glob.glob('/Library/Extensions/*.kext')):
            name = os.path.basename(path)[:-len('.kext')]
            bundle_id = ''
            try:
                with open(os.path.join(path, 'Contents', 'Info.plist'), 'rb') as f:
                    info = plistlib.load(f)
                name = str(info.get('CFBundleName') or name)
                bundle_id = str(info.get('CFBundleIdentifier') or '')
            except Exception:
                pass
            entries.append(StartupEntry(
                name, bundle_id or path, path, 'Kernel Extension', True,
                'Installed driver that macOS loads at startup once approved in Privacy & '
                'Security. Remove it with the uninstaller of the app that added it.'))
        return entries

    @staticmethod
    def _scan_system_extensions() -> List[StartupEntry]:
        try:
            out = _run(['systemextensionsctl', 'list']).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        entries = []
        for line in out.splitlines():
            parts = line.split('\t')
            if len(parts) < 6 or not parts[-1].startswith('['):
                continue
            state = parts[-1].strip('[] ')
            entries.append(StartupEntry(
                parts[4].strip() or parts[3].strip(), parts[3].strip(), f'Team ID {parts[2].strip()}',
                'System Extension', 'enabled' in state.split(),
                f'State: {state}. Manage it in System Settings › General › Login Items & '
                'Extensions.'))
        return entries

    # ── enable / disable ──────────────────────────────────────────────────────

    def can_toggle(self, entry: StartupEntry) -> bool:
        if entry.location_type not in self.TOGGLE_TYPES:
            return False
        if entry.location_type == 'Login Item':
            return bool(entry.extra.get('path'))
        return bool(_LABEL_RE.fullmatch(entry.extra.get('label', '')))

    def enable_entry(self, entry: StartupEntry) -> Tuple[bool, str]:
        return self._toggle(entry, True)

    def disable_entry(self, entry: StartupEntry) -> Tuple[bool, str]:
        return self._toggle(entry, False)

    def _toggle(self, entry: StartupEntry, enable: bool) -> Tuple[bool, str]:
        if not self.can_toggle(entry):
            return False, f"Entries of type '{entry.location_type}' can't be changed here."
        if entry.location_type == 'Login Item':
            return self._toggle_login_item(entry, enable)
        return self._toggle_launchd(entry, enable)

    @staticmethod
    def _toggle_launchd(entry: StartupEntry, enable: bool) -> Tuple[bool, str]:
        label = entry.extra['label']           # matched _LABEL_RE in can_toggle
        verb = 'enable' if enable else 'disable'
        if entry.extra.get('domain') == 'system':
            domain = 'system'
            ok, err = run_as_admin(f'/bin/launchctl {verb} system/{label}',
                                   f'ST-SoftwareTool wants to {verb} the startup item "{label}".')
            if not ok:
                return False, err
        else:
            domain = _gui_domain()
            try:
                r = _run(['launchctl', verb, f'{domain}/{label}'])
            except (OSError, subprocess.SubprocessError) as exc:
                return False, str(exc)
            if r.returncode != 0:
                return False, (r.stderr or r.stdout or f'launchctl exit code {r.returncode}').strip()
        # Confirm with launchd that the change really took effect
        if _disabled_overrides(domain).get(label) is not (not enable):
            return False, 'macOS did not accept the change.'
        return True, ''

    @staticmethod
    def _toggle_login_item(entry: StartupEntry, enable: bool) -> Tuple[bool, str]:
        path, hidden = entry.extra['path'], bool(entry.extra.get('hidden'))
        saved = _load_disabled_login_items()
        if enable:
            if not os.path.exists(path):
                return False, f'The app is no longer at:\n{path}'
            ok, out = _osascript(
                'tell application "System Events" to make login item at end with properties '
                f'{{path:{applescript_quote(path)}, hidden:{"true" if hidden else "false"}}}')
            if not ok:
                return False, out
            _save_disabled_login_items([i for i in saved if i['path'] != path])
            return True, ''
        # Remember it first, so it can be turned back on even if the app quits midway
        if not any(i['path'] == path for i in saved):
            _save_disabled_login_items(saved + [{'name': entry.name, 'path': path,
                                                 'hidden': hidden}])
        ok, out = _osascript(
            f'tell application "System Events" to delete login item {applescript_quote(entry.name)}')
        if not ok:
            _save_disabled_login_items(saved)
            return False, out
        return True, ''


__all__ = ['MacStartupManager', 'CANCELLED']
