"""Small bridges to macOS AppKit through the Objective-C runtime (ctypes, no PyObjC).

Used for things Qt can't do on its own: hiding the Dock icon while the app runs in the
menu bar, and putting the desktop widget at desktop level. Every function is a no-op that
returns None/False when not on macOS or when the call fails.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Optional

IS_MAC = sys.platform == 'darwin'

# NSApplicationActivationPolicy
REGULAR   = 0   # normal app: Dock icon, app menu
ACCESSORY = 1   # menu-bar app: no Dock icon, windows still allowed

_objc = None


def _runtime():
    global _objc
    if _objc is None:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        _objc = lib
    return _objc


def _send(restype, *argtypes):
    """objc_msgSend with an exact prototype (required on Apple Silicon)."""
    address = ctypes.cast(_runtime().objc_msgSend, ctypes.c_void_p).value
    return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)


def _sel(name: bytes):
    return _runtime().sel_registerName(name)


def _ns_app():
    return _send(ctypes.c_void_p)(_runtime().objc_getClass(b'NSApplication'),
                                  _sel(b'sharedApplication'))


def set_activation_policy(policy: int) -> bool:
    """REGULAR shows the Dock icon, ACCESSORY hides it (the app keeps running)."""
    if not IS_MAC:
        return False
    try:
        return bool(_send(ctypes.c_bool, ctypes.c_long)(_ns_app(), _sel(b'setActivationPolicy:'),
                                                        policy))
    except Exception:
        return False


def activation_policy() -> Optional[int]:
    if not IS_MAC:
        return None
    try:
        return int(_send(ctypes.c_long)(_ns_app(), _sel(b'activationPolicy')))
    except Exception:
        return None


def activate_app() -> None:
    """Bring this app to the front (needed after switching back from ACCESSORY)."""
    if not IS_MAC:
        return
    try:
        _send(None, ctypes.c_bool)(_ns_app(), _sel(b'activateIgnoringOtherApps:'), True)
    except Exception:
        pass


def desktop_icon_level() -> int:
    """The window level of Finder's desktop icons."""
    try:
        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library('CoreGraphics'))
        cg.CGWindowLevelForKey.restype = ctypes.c_int32
        cg.CGWindowLevelForKey.argtypes = [ctypes.c_int32]
        return int(cg.CGWindowLevelForKey(18))         # kCGDesktopIconWindowLevelKey
    except (OSError, TypeError, AttributeError):
        return -2147483603                              # its documented value


def pin_to_desktop(win_id: int) -> Optional[int]:
    """Put a window (Qt winId = its NSView) at desktop level: just above the desktop icons,
    below every window, on all Spaces, outside Mission Control and window cycling.
    Returns the window level now in effect."""
    if not IS_MAC:
        return None
    try:
        window = _send(ctypes.c_void_p)(ctypes.c_void_p(int(win_id)), _sel(b'window'))
        if not window:
            return None
        _send(None, ctypes.c_long)(window, _sel(b'setLevel:'), desktop_icon_level() + 1)
        # CanJoinAllSpaces | Stationary | IgnoresCycle
        _send(None, ctypes.c_ulong)(window, _sel(b'setCollectionBehavior:'), 1 | 16 | 64)
        return int(_send(ctypes.c_long)(window, _sel(b'level')))
    except Exception:
        return None
