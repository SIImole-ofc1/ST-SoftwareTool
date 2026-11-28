"""macOS app icon: the light or the dark rounded ST logo, or the special icon (Settings › App Icon).

The special icons are locked until the user types their code (unlock()): Special's code is
shown after pressing Download on the website, Special 2's is handed out elsewhere. Their
pictures are sealed with a key made from the code (core/vault.py), so they can't be taken out
of the app without it; unlocking keeps that key in the settings.

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
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap

from core import mac_appkit, vault
from core.osinfo import mac_app_bundle

CHOICES = ('auto', 'light', 'dark', 'special', 'special2')
CUSTOM = ('dark', 'special', 'special2')    # carried by the app bundle as a custom icon
SPECIALS = ('special', 'special2')

# SHA-256 of each unlock code (lower-case), so the codes aren't sitting in the app as text
_CODES = {'142a281a530e374416a696516bc7feeeb71e6dc1c7ebeb3498784be73a46a12f': 'special',
          '5564fd4c735f4de2e84a2682980ea80c6efdb71b344aa45c28a72b78b6690505': 'special2'}
_UNLOCKED_KEY = {'special': 'special_icon_unlocked', 'special2': 'special2_icon_unlocked'}


def _keys(settings: dict) -> dict:
    keys = settings.get('special_keys')
    return keys if isinstance(keys, dict) else {}


def special_unlocked(settings: dict, which: str = 'special') -> bool:
    """Unlocked, and the key to its picture is known (unlocked before v1.10.29, when the
    pictures weren't sealed yet: the code has to be typed once more)."""
    return bool(settings.get(_UNLOCKED_KEY[which])) and bool(_keys(settings).get(which))


def needs_code_again(settings: dict, which: str) -> bool:
    return bool(settings.get(_UNLOCKED_KEY[which])) and not _keys(settings).get(which)


def unlock(settings: dict, code: str) -> str | None:
    """Check a code typed in Settings; on success remember which special icon it unlocks (and
    the key that opens its picture) and return its name ('special' / 'special2'), else None.
    Capitals and spaces around it don't matter."""
    normal = (code or '').strip().lower()
    which = _CODES.get(hashlib.sha256(normal.encode('utf-8')).hexdigest())
    if not which:
        return None
    key = vault.special_key(normal, which)
    if vault.special(which, key.hex()) is None:          # the picture must open with it
        return None
    settings[_UNLOCKED_KEY[which]] = True
    settings['special_keys'] = {**_keys(settings), which: key.hex()}
    return which


def icon_data(style: str, settings: dict | None = None) -> bytes | None:
    """The picture of an icon style: light / dark / special / special2."""
    if style in SPECIALS:
        return vault.special(style, _keys(settings or {}).get(style))
    return vault.data(f'app-icon-{style}.png')


def qicon(style: str, settings: dict | None = None) -> QIcon:
    raw = icon_data(style, settings)
    pm = QPixmap()
    if raw:
        pm.loadFromData(raw)
    return QIcon(pm) if not pm.isNull() else QIcon()

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
    raw = icon_data(style, settings)
    app = QGuiApplication.instance()
    if app is not None and raw:
        app.setWindowIcon(qicon(style, settings))        # the Dock icon while ST runs

    bundle = mac_app_bundle()       # the installed app only — never Python.app from source
    if bundle:
        applied = settings.get('app_icon_bundle', 'light')
        if style in CUSTOM and raw and (applied != style or not _custom_icon_present(bundle)):
            if mac_appkit.set_file_icon(bundle, vault.file(f'app-icon-{style}.png', raw)):
                settings['app_icon_bundle'] = style
        elif style == 'light' and (applied != 'light' or _custom_icon_present(bundle)):
            if mac_appkit.set_file_icon(bundle, None):
                settings['app_icon_bundle'] = 'light'
    return style
