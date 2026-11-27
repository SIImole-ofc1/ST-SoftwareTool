"""Now playing, playback controls and volume for the Music & Audio desktop widget (macOS).

Apple Music and Spotify are asked over AppleScript — only when they are already running, so
the widget never launches a player. macOS asks once for permission to control each app.
Players without a Mac app (SoundCloud, YouTube … in a browser) can't be read by other apps on
current macOS, but they react to the media keys, which the widget's buttons then send.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import tempfile
import urllib.request
from typing import Optional

from core.osinfo import IS_MAC

PLAYERS = (('Music', 'Apple Music'), ('Spotify', 'Spotify'))
_ART_DIR = os.path.join(tempfile.gettempdir(), 'st-nowplaying')
_SEP = '\x1f'


def _osa(script: str, timeout: float = 4.0) -> tuple:
    r = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def running_players() -> list:
    """App names (Music / Spotify) that are running now."""
    try:
        import psutil
        names = {p.info['name'] for p in psutil.process_iter(['name'])}
    except Exception:
        return []
    return [app for app, _ in PLAYERS if app in names]


_INFO = {
    'Music': f'''tell application "Music"
  set s to player state as text
  if s is "stopped" then return s
  set t to current track
  return s & "{_SEP}" & name of t & "{_SEP}" & artist of t & "{_SEP}" & album of t & "{_SEP}" & (duration of t) & "{_SEP}" & player position & "{_SEP}" & ""
end tell''',
    'Spotify': f'''tell application "Spotify"
  set s to player state as text
  if s is "stopped" then return s
  set t to current track
  return s & "{_SEP}" & name of t & "{_SEP}" & artist of t & "{_SEP}" & album of t & "{_SEP}" & ((duration of t) / 1000) & "{_SEP}" & player position & "{_SEP}" & artwork url of t
end tell''',
}


def _num(text: str) -> float:
    try:
        return float(text.replace(',', '.'))
    except ValueError:
        return 0.0


def _artwork(app: str, key: str, url: str) -> Optional[str]:
    """A cached image file for the current track's cover."""
    os.makedirs(_ART_DIR, exist_ok=True)
    path = os.path.join(_ART_DIR, hashlib.sha1(f'{app}|{key}'.encode()).hexdigest() + '.img')
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    try:
        if app == 'Spotify' and url.startswith('https://'):
            req = urllib.request.Request(url, headers={'User-Agent': 'ST-SoftwareTool'})
            with urllib.request.urlopen(req, timeout=6) as resp, open(path, 'wb') as f:
                f.write(resp.read(5_000_000))
            return path
        if app == 'Music':
            target = path.replace('"', '')
            code, _, _ = _osa(f'''tell application "Music"
  if (count of artworks of current track) is 0 then return ""
  set d to raw data of artwork 1 of current track
end tell
set f to open for access (POSIX file "{target}") with write permission
set eof f to 0
write d to f
close access f''', timeout=6)
            if code == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
                return path
    except Exception:
        pass
    return None


def now_playing() -> dict:
    """What's playing: {app, label, state, title, artist, album, duration, position, artwork}
    or {state: 'none'} / {state: 'denied', app} when macOS hasn't allowed control yet."""
    if not IS_MAC:
        return {'state': 'none'}
    found = []
    for app in running_players():
        try:
            code, out, err = _osa(_INFO[app])
        except subprocess.TimeoutExpired:
            continue
        if code != 0:
            if '-1743' in err or 'Not authorized' in err:
                return {'state': 'denied', 'app': app, 'label': dict(PLAYERS)[app]}
            continue
        parts = out.split(_SEP)
        if parts[0] == 'stopped' or len(parts) < 7:
            found.append({'app': app, 'label': dict(PLAYERS)[app], 'state': 'stopped'})
            continue
        state, title, artist, album, dur, pos, art = parts[:7]
        found.append({'app': app, 'label': dict(PLAYERS)[app], 'state': state, 'title': title,
                      'artist': artist, 'album': album, 'duration': _num(dur),
                      'position': _num(pos), 'art_url': art})
    if not found:
        return {'state': 'none'}
    found.sort(key=lambda d: {'playing': 0, 'paused': 1}.get(d['state'], 2))
    best = found[0]
    if best.get('title'):
        best['artwork'] = _artwork(best['app'], f"{best['title']}|{best['artist']}|{best.get('album')}",
                                   best.get('art_url', ''))
    return best


def control(action: str, app: Optional[str] = None) -> bool:
    """action: playpause / next / previous. Goes to the app, or to the media keys."""
    if not IS_MAC:
        return False
    if app in dict(PLAYERS) and app in running_players():
        verb = {'playpause': 'playpause', 'next': 'next track', 'previous': 'previous track'}[action]
        try:
            return _osa(f'tell application "{app}" to {verb}')[0] == 0
        except subprocess.TimeoutExpired:
            return False
    return media_key(action)


# ── volume ───────────────────────────────────────────────────────────────────

def volume() -> tuple:
    """(output volume 0–100, muted)."""
    if not IS_MAC:
        return 0, False
    try:
        code, out, _ = _osa('set v to get volume settings\n'
                            f'return ((output volume of v) as text) & "{_SEP}" & ((output muted of v) as text)')
        vol, muted = out.split(_SEP)
        return int(_num(vol)), muted == 'true'
    except Exception:
        return 0, False


def set_volume(level: int) -> None:
    if IS_MAC:
        level = max(0, min(100, int(level)))
        _osa(f'set volume output volume {level} without output muted')


def toggle_mute() -> None:
    if IS_MAC:
        _osa('set volume output muted (not (output muted of (get volume settings)))')


# ── media keys (browser players: SoundCloud, YouTube …) ──────────────────────

_KEYS = {'playpause': 16, 'next': 17, 'previous': 18}        # NX_KEYTYPE_PLAY / NEXT / PREVIOUS


def accessibility_allowed() -> bool:
    """Sending media keys needs Privacy & Security › Accessibility for ST-SoftwareTool."""
    try:
        ax = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    except Exception:
        return False


def media_key(action: str) -> bool:
    """Press and release a media key, like the keyboard's ⏯ ⏭ ⏮."""
    from core import mac_appkit as ak
    try:
        cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

        class _NSPoint(ctypes.Structure):
            _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
        key = _KEYS[action]
        make = ak._send(ctypes.c_void_p, ctypes.c_ulong, _NSPoint, ctypes.c_ulong, ctypes.c_double,
                        ctypes.c_long, ctypes.c_void_p, ctypes.c_short, ctypes.c_long, ctypes.c_long)
        sel = ak._sel(b'otherEventWithType:location:modifierFlags:timestamp:windowNumber:'
                      b'context:subtype:data1:data2:')
        for down in (True, False):
            flags = 0xa00 if down else 0xb00
            data1 = (key << 16) | ((0xa if down else 0xb) << 8)
            event = make(ak._cls(b'NSEvent'), sel, 14, _NSPoint(0, 0), flags, 0.0, 0, None, 8,
                         data1, -1)
            cg_event = ak._send(ctypes.c_void_p)(event, ak._sel(b'CGEvent'))
            cg.CGEventPost(0, cg_event)          # kCGHIDEventTap
        return True
    except Exception:
        return False
