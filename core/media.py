"""Now playing, playback controls and volume for the Music & Audio desktop widget (macOS).

Main source: macOS's own Now Playing — the same one Control Center shows — so it covers every
player: Apple Music, Spotify, YouTube / SoundCloud / … in Safari, Chrome or Firefox, VLC and
so on, with the cover, and the buttons control that player directly (no permission needed).
Since macOS 15.4 only Apple-signed programs may use it, so our small helper library
(packaging/macos/nowplaying) is loaded into the system's /usr/bin/perl.

Fallback when the helper isn't there: Apple Music and Spotify over AppleScript (only when
already running, so the widget never launches a player), and the media keys.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Optional

from core.osinfo import IS_MAC

PLAYERS = (('Music', 'Apple Music'), ('Spotify', 'Spotify'))
_ART_DIR = os.path.join(tempfile.gettempdir(), 'st-nowplaying')
_SEP = '\x1f'


# ── macOS Now Playing (through the helper) ───────────────────────────────────

_HELPER_NAME = 'st_nowplaying.dylib'
_PERL_LOADER = ('use DynaLoader; my $l = DynaLoader::dl_load_file($ARGV[0], 0) or die; '
                'my $s = DynaLoader::dl_find_symbol($l, "st_nowplaying") or die; '
                'DynaLoader::dl_install_xsub("main::st_np", $s); main::st_np();')
_BROWSERS = {'org.mozilla.firefox', 'com.google.Chrome', 'com.apple.Safari', 'com.microsoft.edgemac',
             'com.brave.Browser', 'company.thebrowser.Browser', 'com.operasoftware.Opera',
             'com.vivaldi.Vivaldi', 'org.chromium.Chromium', 'app.zen-browser.zen'}


def helper_path() -> Optional[str]:
    """The Now Playing helper: in the app (Contents/MacOS/tools), or build/tools in a checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'tools', _HELPER_NAME),
                 os.path.join(here, '..', 'tools', _HELPER_NAME),
                 os.path.join(here, '..', 'build', 'tools', _HELPER_NAME)):
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def _helper(cmd: str, timeout: float = 5.0) -> Optional[dict]:
    """Run the helper once: 'get', or a command. None when it can't run."""
    path = helper_path()
    if not IS_MAC or not path or not os.path.exists('/usr/bin/perl'):
        return None
    os.makedirs(_ART_DIR, exist_ok=True)
    env = dict(os.environ, ST_NP_CMD=cmd, ST_NP_ART_DIR=_ART_DIR)
    env.pop('PERL5LIB', None)
    env.pop('PERL5OPT', None)
    try:
        r = subprocess.run(['/usr/bin/perl', '-e', _PERL_LOADER, path], capture_output=True,
                           text=True, timeout=timeout, env=env)
        line = (r.stdout or '').strip().splitlines()
        data = json.loads(line[-1]) if line else None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return data if isinstance(data, dict) and 'error' not in data else None


def _from_system(d: dict) -> dict:
    """The helper's answer → the widget's now-playing dict."""
    if d.get('state') == 'none' or not (d.get('title') or d.get('artist')):
        return {'state': 'none'}
    rate = d.get('rate') or 0
    playing = d.get('playing') if d.get('playing') is not None else rate > 0
    position = float(d.get('elapsed') or 0)
    if playing and d.get('timestamp'):            # elapsed is "as of timestamp"
        position += max(0.0, time.time() - float(d['timestamp'])) * (rate or 1)
    duration = float(d.get('duration') or 0)
    if duration:
        position = min(position, duration)
    app = d.get('app') or 'Now Playing'
    label = {'Music': 'Apple Music'}.get(app, app)
    if d.get('bundle') in _BROWSERS:
        label = f'{app} · browser'
    return {'app': app, 'bundle': d.get('bundle', ''), 'label': label, 'source': 'system',
            'state': 'playing' if playing else 'paused', 'title': d.get('title') or '',
            'artist': d.get('artist') or '', 'album': d.get('album') or '',
            'duration': duration, 'position': position, 'artwork': d.get('artwork')}


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
    system = _helper('get')
    if system is not None:
        return _from_system(system)
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
    sent = _helper(action)
    if sent is not None and sent.get('ok'):
        return True
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
