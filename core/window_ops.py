"""
Win32 window operations: close and minimize running app windows.
"""
import ctypes
import ctypes.wintypes
import subprocess
from typing import List, Tuple

SW_MINIMIZE = 6
WM_CLOSE    = 0x0010

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


def close_app(name: str) -> Tuple[bool, str]:
    """Close all visible windows whose title contains `name`."""
    hwnds = _matching_hwnds(name)
    if hwnds:
        for h in hwnds:
            _u32.PostMessageW(h, WM_CLOSE, 0, 0)
        return True, f"Sent close to {len(hwnds)} window(s) matching '{name}'."
    # Fallback: taskkill by image name
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/FI", f"IMAGENAME eq *{name}*"],
            capture_output=True, text=True,
        )
        if "SUCCESS" in r.stdout:
            return True, f"Killed process matching '{name}'."
    except Exception:
        pass
    return False, f"No window or process found matching '{name}'."


def min_app(name: str) -> Tuple[bool, str]:
    """Minimize all visible windows whose title contains `name`."""
    hwnds = _matching_hwnds(name)
    if not hwnds:
        return False, f"No visible window found for '{name}'."
    for h in hwnds:
        _u32.ShowWindow(h, SW_MINIMIZE)
    return True, f"Minimized {len(hwnds)} window(s) matching '{name}'."
