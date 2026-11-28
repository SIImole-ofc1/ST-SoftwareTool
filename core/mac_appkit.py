"""Small bridges to macOS AppKit through the Objective-C runtime (ctypes, no PyObjC).

Used for things Qt can't do on its own: hiding the Dock icon while the app runs in the
menu bar, putting the desktop widget at desktop level, and the native frosted-glass blur
behind the macOS Glass theme and the widget. Every function is a no-op that
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


def _nsstring(text: str):
    return _send(ctypes.c_void_p, ctypes.c_char_p)(_runtime().objc_getClass(b'NSString'),
                                                   _sel(b'stringWithUTF8String:'),
                                                   text.encode('utf-8'))


def set_file_icon(path: str, image_path: Optional[str]) -> bool:
    """Set the icon Finder, Launchpad and the Dock show for a file or app bundle (what
    "Get Info › paste icon" does). image_path=None removes the custom icon again."""
    if not IS_MAC:
        return False
    try:
        workspace = _send(ctypes.c_void_p)(_runtime().objc_getClass(b'NSWorkspace'),
                                           _sel(b'sharedWorkspace'))
        image = None
        if image_path:
            image = _send(ctypes.c_void_p)(_runtime().objc_getClass(b'NSImage'), _sel(b'alloc'))
            image = _send(ctypes.c_void_p, ctypes.c_void_p)(image, _sel(b'initWithContentsOfFile:'),
                                                            _nsstring(image_path))
            if not image:
                return False
        ok = _send(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
            workspace, _sel(b'setIcon:forFile:options:'), image, _nsstring(path), 0)
        if image:
            _send(None)(image, _sel(b'release'))
        return bool(ok)
    except Exception:
        return False


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


# ── frosted glass (NSVisualEffectView) ───────────────────────────────────────

class _NSRect(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double),
                ('w', ctypes.c_double), ('h', ctypes.c_double)]


class _NSSize(ctypes.Structure):
    _fields_ = [('w', ctypes.c_double), ('h', ctypes.c_double)]


class _NSEdgeInsets(ctypes.Structure):
    _fields_ = [('top', ctypes.c_double), ('left', ctypes.c_double),
                ('bottom', ctypes.c_double), ('right', ctypes.c_double)]


def _bounds(view) -> _NSRect:
    """[view bounds]. Intel Macs return a 32-byte struct through objc_msgSend_stret."""
    import platform
    name = 'objc_msgSend_stret' if platform.machine() == 'x86_64' else 'objc_msgSend'
    address = ctypes.cast(getattr(_runtime(), name), ctypes.c_void_p).value
    fn = ctypes.CFUNCTYPE(_NSRect, ctypes.c_void_p, ctypes.c_void_p)(address)
    return fn(view, _sel(b'bounds'))


def _cls(name: bytes):
    return _runtime().objc_getClass(name)


def blur_supported() -> bool:
    if not IS_MAC:
        return False
    try:
        return bool(_cls(b'NSVisualEffectView'))
    except Exception:
        return False


_accent_cache = (-1e9, None)


def accent_color() -> Optional[str]:
    """The accent colour chosen in System Settings › Appearance, as '#rrggbb'. Widgets ask on
    every repaint, so the answer is kept for a few seconds."""
    global _accent_cache
    if not IS_MAC:
        return None
    import time
    when, value = _accent_cache
    if time.monotonic() - when < 5:
        return value
    value = _ask_accent_color()
    _accent_cache = (time.monotonic(), value)
    return value


def _ask_accent_color() -> Optional[str]:
    try:
        color = _send(ctypes.c_void_p)(_cls(b'NSColor'), _sel(b'controlAccentColor'))
        srgb = _send(ctypes.c_void_p)(_cls(b'NSColorSpace'), _sel(b'sRGBColorSpace'))
        color = _send(ctypes.c_void_p, ctypes.c_void_p)(color, _sel(b'colorUsingColorSpace:'), srgb)
        if not color:
            return None
        parts = [_send(ctypes.c_double)(color, _sel(name))
                 for name in (b'redComponent', b'greenComponent', b'blueComponent')]
        return '#' + ''.join(f'{max(0, min(255, round(c * 255))):02x}' for c in parts)
    except Exception:
        return None


class BlurBackdrop:
    """Native frosted glass behind a top-level Qt window — the blur macOS uses for sidebars,
    Control Center and widgets. Qt paints on top of it, so the window must be translucent
    (Qt.WA_TranslucentBackground) and paint only see-through colours where the blur should show.

    mask_png: optional PNG (e.g. a rounded rectangle) that limits the blur to its opaque
    pixels; cap_inset is the corner size in points that stays unstretched (a 9-slice image).
    """

    _UNDER_WINDOW  = 21        # NSVisualEffectMaterialUnderWindowBackground
    _HUD           = 13        # NSVisualEffectMaterialHUDWindow
    _BEHIND_WINDOW = 0         # NSVisualEffectBlendingModeBehindWindow
    _ACTIVE        = 1         # stay frosted while the app is in the background
    _SIZABLE       = 2 | 16    # NSViewWidthSizable | NSViewHeightSizable
    _BELOW         = -1        # NSWindowBelow

    def __init__(self, material: str = 'window', mask_png: Optional[bytes] = None,
                 mask_scale: float = 2.0, cap_inset: float = 0.0):
        self._material = self._HUD if material == 'hud' else self._UNDER_WINDOW
        self._mask_png, self._mask_scale, self._cap = mask_png, mask_scale, cap_inset
        self._view = None
        self._window = None
        self._failed = False

    @property
    def attached(self) -> bool:
        return self._view is not None

    def attach(self, win_id: int) -> bool:
        """Put the blur view behind the window's content. Needs the native window to exist
        (call after show). Safe to call again; it attaches once."""
        if self._view is not None:
            return True
        if self._failed or not blur_supported():
            return False
        try:
            window = _send(ctypes.c_void_p)(ctypes.c_void_p(int(win_id)), _sel(b'window'))
            if not window:
                return False
            content = _send(ctypes.c_void_p)(window, _sel(b'contentView'))
            frame = _send(ctypes.c_void_p)(content, _sel(b'superview'))
            view = _send(ctypes.c_void_p)(_cls(b'NSVisualEffectView'), _sel(b'alloc'))
            view = _send(ctypes.c_void_p, _NSRect)(view, _sel(b'initWithFrame:'), _bounds(frame))
            _send(None, ctypes.c_ulong)(view, _sel(b'setAutoresizingMask:'), self._SIZABLE)
            _send(None, ctypes.c_long)(view, _sel(b'setBlendingMode:'), self._BEHIND_WINDOW)
            _send(None, ctypes.c_long)(view, _sel(b'setMaterial:'), self._material)
            _send(None, ctypes.c_long)(view, _sel(b'setState:'), self._ACTIVE)
            if self._mask_png:
                image = self._mask_image()
                if image:
                    _send(None, ctypes.c_void_p)(view, _sel(b'setMaskImage:'), image)
            _send(None, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p)(
                frame, _sel(b'addSubview:positioned:relativeTo:'), view, self._BELOW, content)
            _send(None, ctypes.c_bool)(window, _sel(b'setOpaque:'), False)
            _send(None, ctypes.c_void_p)(window, _sel(b'setBackgroundColor:'),
                                         _send(ctypes.c_void_p)(_cls(b'NSColor'), _sel(b'clearColor')))
            self._view, self._window = view, window
            return True
        except Exception as exc:
            self._failed = True
            print(f'[glass] native blur unavailable: {exc}', file=sys.stderr)
            return False

    def _mask_image(self):
        data = ctypes.create_string_buffer(self._mask_png, len(self._mask_png))
        nsdata = _send(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
            _cls(b'NSData'), _sel(b'dataWithBytes:length:'), data, len(self._mask_png))
        image = _send(ctypes.c_void_p)(_cls(b'NSImage'), _sel(b'alloc'))
        image = _send(ctypes.c_void_p, ctypes.c_void_p)(image, _sel(b'initWithData:'), nsdata)
        if not image:
            return None
        # Point size = pixel size / scale, so a 2x PNG stays sharp on Retina screens.
        rep = _send(ctypes.c_void_p)(image, _sel(b'representations'))
        first = _send(ctypes.c_void_p)(rep, _sel(b'firstObject'))
        px_w = _send(ctypes.c_long)(first, _sel(b'pixelsWide'))
        px_h = _send(ctypes.c_long)(first, _sel(b'pixelsHigh'))
        _send(None, _NSSize)(image, _sel(b'setSize:'),
                             _NSSize(px_w / self._mask_scale, px_h / self._mask_scale))
        c = self._cap
        _send(None, _NSEdgeInsets)(image, _sel(b'setCapInsets:'), _NSEdgeInsets(c, c, c, c))
        _send(None, ctypes.c_long)(image, _sel(b'setResizingMode:'), 1)   # stretch the middle
        return image

    def update(self, visible: bool, alpha: float = 1.0, dark: Optional[bool] = None,
               transparent_titlebar: Optional[bool] = None) -> bool:
        """Show / hide the blur and set its strength (alpha 0 = clear, 1 = fully frosted)."""
        if self._view is None:
            return False
        try:
            _send(None, ctypes.c_bool)(self._view, _sel(b'setHidden:'), not visible)
            if transparent_titlebar is not None:
                _send(None, ctypes.c_bool)(self._window, _sel(b'setTitlebarAppearsTransparent:'),
                                           bool(transparent_titlebar))
            if not visible:
                return True
            _send(None, ctypes.c_double)(self._view, _sel(b'setAlphaValue:'),
                                         max(0.0, min(1.0, float(alpha))))
            appearance = None
            if dark is not None:
                name = 'NSAppearanceNameDarkAqua' if dark else 'NSAppearanceNameAqua'
                appearance = _send(ctypes.c_void_p, ctypes.c_void_p)(
                    _cls(b'NSAppearance'), _sel(b'appearanceNamed:'), _nsstring(name))
            _send(None, ctypes.c_void_p)(self._view, _sel(b'setAppearance:'), appearance)
            return True
        except Exception as exc:
            print(f'[glass] backdrop update failed: {exc}', file=sys.stderr)
            return False

    def is_visible(self) -> Optional[bool]:
        """For checks: whether the blur view is attached and shown (None = not attached)."""
        if self._view is None:
            return None
        return not bool(_send(ctypes.c_bool)(self._view, _sel(b'isHidden')))

    def alpha(self) -> Optional[float]:
        if self._view is None:
            return None
        return float(_send(ctypes.c_double)(self._view, _sel(b'alphaValue')))
