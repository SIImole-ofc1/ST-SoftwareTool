"""
Window operations: close and minimize running app windows.

Windows uses Win32 window messages; macOS uses AppleScript; other systems fall back
to ending the process (close) and report that minimizing is unsupported.
"""
import subprocess
import sys
from typing import List, Tuple

SW_MINIMIZE = 6
WM_CLOSE    = 0x0010

if sys.platform == 'win32':
    import ctypes
    import ctypes.wintypes

    _u32 = ctypes.windll.user32
    _EnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def _matching_hwnds(name: str) -> List[int]:
        needle = name.lower()
        found: List[int] = []

        def _cb(hwnd, _):
            if _u32.IsWindowVisible(hwnd):
                n = _u32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    _u32.GetWindowTextW(hwnd, buf, n + 1)
                    if needle in buf.value.lower():
                        found.append(hwnd)
            return True

        _u32.EnumWindows(_EnumProc(_cb), 0)
        return found
else:
    def _matching_hwnds(name: str) -> List[int]:
        return []


def _osascript(script: str) -> bool:
    try:
        r = subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mac_app_name(name: str) -> str:
    return name.replace('"', '').strip()


def close_app(name: str) -> Tuple[bool, str]:
    """Close all visible windows (Windows) / quit the app (macOS) matching `name`."""
    if sys.platform == 'win32':
        hwnds = _matching_hwnds(name)
        if hwnds:
            for h in hwnds:
                _u32.PostMessageW(h, WM_CLOSE, 0, 0)
            return True, f"Sent close to {len(hwnds)} window(s) matching '{name}'."
    elif sys.platform == 'darwin':
        if _osascript(f'quit app "{_mac_app_name(name)}"'):
            return True, f"Asked '{name}' to quit."
    # Fallback: kill by process name using psutil (supports partial matching)
    try:
        import psutil
        name_lower = name.lower()
        killed = 0
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if name_lower in (proc.info.get('name') or '').lower():
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            return True, f"Killed {killed} process(es) matching '{name}'."
    except Exception:
        pass
    return False, f"No window or process found matching '{name}'."


def min_app(name: str) -> Tuple[bool, str]:
    """Minimize (Windows) / hide (macOS) the windows of the app matching `name`."""
    if sys.platform == 'darwin':
        script = (f'tell application "System Events" to set visible of '
                  f'process "{_mac_app_name(name)}" to false')
        if _osascript(script):
            return True, f"Hid '{name}'."
        return False, f"No running app found for '{name}'."
    if sys.platform != 'win32':
        return False, 'Minimizing other apps is not supported on this system.'
    hwnds = _matching_hwnds(name)
    if not hwnds:
        return False, f"No visible window found for '{name}'."
    for h in hwnds:
        _u32.ShowWindow(h, SW_MINIMIZE)
    return True, f"Minimized {len(hwnds)} window(s) matching '{name}'."
