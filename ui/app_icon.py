"""macOS app icon: the light or the dark rounded ST logo, or the special icon (Settings › App Icon).

The special icons are locked until the user types their code (unlock()): Special's code is
shown after pressing Download on the website, Special 2's is handed out elsewhere.

Automatic follows the Mac: the dark logo when macOS shows dark app icons (macOS 26 "Icon &
widget style: Dark") or, when that isn't set, while Dark Mode is on.

The chosen icon is shown in the Dock right away. So that Finder, Launchpad and the Dock (when
ST isn't running) show it too, the dark logo is set as the app's custom icon — the light logo
is the app's built-in icon, so choosing it simply removes the custom one again.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon

from core import mac_appkit
from core.osinfo import mac_app_bundle

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
ICONS = {'light':   os.path.join(_ASSETS, 'app-icon-light.png'),
         'dark':    os.path.join(_ASSETS, 'app-icon-dark.png'),
         'special': os.path.join(_ASSETS, 'app-icon-special.png'),
         'special2': os.path.join(_ASSETS, 'app-icon-special2.png')}
CHOICES = ('auto', 'light', 'dark', 'special', 'special2')
CUSTOM = ('dark', 'special', 'special2')    # carried by the app bundle as a custom icon
SPECIALS = ('special', 'special2')

# SHA-256 of each unlock code (lower-case), so the codes aren't sitting in the app as text
_CODES = {'142a281a530e374416a696516bc7feeeb71e6dc1c7ebeb3498784be73a46a12f': 'special',
          '5564fd4c735f4de2e84a2682980ea80c6efdb71b344aa45c28a72b78b6690505': 'special2'}
_UNLOCKED_KEY = {'special': 'special_icon_unlocked', 'special2': 'special2_icon_unlocked'}


def special_unlocked(settings: dict, which: str = 'special') -> bool:
    return bool(settings.get(_UNLOCKED_KEY[which]))


def unlock(settings: dict, code: str) -> str | None:
    """Check a code typed in Settings; on success remember which special icon it unlocks and
    return its name ('special' / 'special2'), else None. Capitals and spaces around it don't
    matter."""
    normal = (code or '').strip().lower()
    which = _CODES.get(hashlib.sha256(normal.encode('utf-8')).hexdigest())
    if which:
        settings[_UNLOCKED_KEY[which]] = True
    return which

_theme_cache = (0.0, '')


def _icon_theme() -> str:
    """macOS 26 icon style, e.g. RegularDark / ClearLight / TintedAutomatic; '' if not set."""
    global _theme_cache
    checked, value = _theme_cache
    if time.monotonic() - checked < 5:
        return value
    try:
        r = subprocess.run(['/usr/bin/defaults', 'read', '-g', 'AppleIconAppearanceTheme'],
                           capture_output=True, text=True, timeout=5)
        value = r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        value = ''
    _theme_cache = (time.monotonic(), value)
    return value


def system_wants_dark() -> bool:
    theme = _icon_theme()
    if 'Dark' in theme:
        return True
    if 'Light' in theme:
        return False
    try:   # no explicit icon style: follow Dark Mode
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def resolve(choice: str, settings: dict | None = None) -> str:
    if choice in ('light', 'dark'):
        return choice
    if choice in SPECIALS and settings is not None and special_unlocked(settings, choice):
        return choice
    return 'dark' if system_wants_dark() else 'light'


def _custom_icon_present(bundle: str) -> bool:
    return os.path.exists(os.path.join(bundle, 'Icon\r'))


def apply(settings: dict) -> str:
    """Show the icon chosen in settings['app_icon'] (auto / light / dark / special). Returns
    the style in effect. Remembers in settings['app_icon_bundle'] what the app bundle carries."""
    style = resolve(settings.get('app_icon', 'auto'), settings)
    app = QGuiApplication.instance()
    if app is not None and os.path.exists(ICONS[style]):
        app.setWindowIcon(QIcon(ICONS[style]))           # the Dock icon while ST runs

    bundle = mac_app_bundle()       # the installed app only — never Python.app from source
    if bundle:
        applied = settings.get('app_icon_bundle', 'light')
        if style in CUSTOM and (applied != style or not _custom_icon_present(bundle)):
            if mac_appkit.set_file_icon(bundle, ICONS[style]):
                settings['app_icon_bundle'] = style
        elif style == 'light' and (applied != 'light' or _custom_icon_present(bundle)):
            if mac_appkit.set_file_icon(bundle, None):
                settings['app_icon_bundle'] = 'light'
    return style
