"""Delete ST-SoftwareTool from this computer (Settings › Delete ST-SoftwareTool).

macOS:   the login item goes, the settings / data folder is deleted and the app is moved to
         the Trash (so it can still be put back); then the app quits.
Windows: the startup entry goes, then the app's own uninstaller (unins000.exe, from the
         installer) runs and removes the program files and data while the app quits.
Homebrew and anything else the user installed stays.
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Tuple

from core.osinfo import IS_MAC, IS_WINDOWS, mac_app_bundle, own_executable


def data_dir() -> str:
    if IS_WINDOWS:
        return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'ST-SoftwareTool')
    if IS_MAC:
        return os.path.expanduser('~/Library/Application Support/ST-SoftwareTool')
    base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, 'ST-SoftwareTool')


def windows_uninstaller() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    path = os.path.join(os.path.dirname(own_executable()), 'unins000.exe')
    return path if os.path.isfile(path) else None


def installed_copy() -> Optional[str]:
    """What gets removed: the .app bundle (macOS) or the install folder (Windows).
    None when running from source, where there is nothing to uninstall."""
    if IS_MAC:
        return mac_app_bundle()
    if IS_WINDOWS:
        return os.path.dirname(own_executable()) if windows_uninstaller() else None
    return None


def can_uninstall() -> bool:
    return installed_copy() is not None


def plan() -> List[str]:
    """What will be removed, for the confirmation dialog."""
    items = []
    copy = installed_copy()
    if IS_MAC:
        items.append(f'The app  ({copy})  — moved to the Trash')
        items.append('Its settings, app list, antivirus quarantine, VPN data and logs  '
                     f'({data_dir().replace(os.path.expanduser("~"), "~")})')
        items.append('Opening at login, the menu-bar icon and the desktop widget')
    elif IS_WINDOWS:
        items.append(f'The program  ({copy})')
        items.append(f'Its settings, app list, antivirus quarantine, VPN data and logs  ({data_dir()})')
        items.append('Starting with Windows')
    return items


def _trash_mac(path: str) -> Tuple[bool, str]:
    """Move to the Trash through NSFileManager (no Finder permission prompt)."""
    import ctypes
    from core import mac_appkit as ak
    try:
        url = ak._send(ctypes.c_void_p, ctypes.c_void_p)(
            ak._cls(b'NSURL'), ak._sel(b'fileURLWithPath:'), ak._nsstring(path))
        fm = ak._send(ctypes.c_void_p)(ak._cls(b'NSFileManager'), ak._sel(b'defaultManager'))
        ok = ak._send(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            fm, ak._sel(b'trashItemAtURL:resultingItemURL:error:'), url, None, None)
        if ok:
            return True, 'moved to the Trash'
    except Exception:
        pass
    trash = os.path.expanduser('~/.Trash')
    try:
        dest = os.path.join(trash, os.path.basename(path))
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(trash, f'{os.path.basename(path)[:-4]} {n}.app')
        shutil.move(path, dest)
        return True, 'moved to the Trash'
    except OSError as exc:
        return False, f'could not move it to the Trash ({exc})'


def _remove_startup() -> None:
    if IS_MAC:
        try:
            os.remove(os.path.expanduser('~/Library/LaunchAgents/com.stsoftwaretool.app.plist'))
        except OSError:
            pass
    elif IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 0,
                                 winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, 'ST-SoftwareTool')
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except OSError:
            pass


def uninstall(silent_windows: bool = True) -> Tuple[bool, str]:
    """Remove ST-SoftwareTool. The caller quits the app right after a True result."""
    copy = installed_copy()
    if copy is None:
        return False, ('This copy runs from source code, so there is nothing to uninstall. '
                       'Delete the project folder to remove it.')
    _remove_startup()
    if IS_MAC:
        ok, how = _trash_mac(copy)
        if not ok:
            return False, f'ST-SoftwareTool was not removed: {how}.'
        shutil.rmtree(data_dir(), ignore_errors=True)
        return True, f'ST-SoftwareTool was {how}, and its settings and data were deleted.'
    if IS_WINDOWS:
        import ctypes
        exe = windows_uninstaller()
        args = '/SILENT /SUPPRESSMSGBOXES /NORESTART' if silent_windows else ''
        # ShellExecute, so Windows shows the administrator (UAC) prompt the uninstaller needs
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'open', exe, args,
                                                 os.path.dirname(exe), 1)
        if rc <= 32:
            return False, f'The uninstaller could not be started (error {rc}).'
        return True, 'The uninstaller is removing ST-SoftwareTool.'
    return False, 'Uninstalling is supported on macOS and Windows.'
