"""Homebrew for macOS: find it, and install it for people who don't have it yet.

Installing never happens silently — the user asks for it (first-launch offer, Settings, or
`>_/brew:install` in the terminal) and macOS asks for their administrator password.

* Apple Silicon Mac on macOS 15 or newer: download Homebrew's official .pkg from its GitHub
  release, check it (GitHub's SHA-256, Apple notarization, and that Homebrew's Developer ID
  signed it), then run Apple's `installer` with the standard password dialog.
* Any other Mac (Intel, or older macOS — the .pkg doesn't support them): open Terminal with
  Homebrew's official install command, which asks its own questions there.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Callable, Optional, Tuple

from core.osinfo import IS_MAC

PREFIXES = ('/opt/homebrew', '/usr/local')            # Apple Silicon, Intel
_RELEASES_API = 'https://api.github.com/repos/Homebrew/brew/releases/latest'
_INSTALL_SH = 'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh'
# Developer ID team that signs Homebrew.pkg (checked together with Apple notarization)
_PKG_TEAM_IDS = {'927JGANW46'}
_USER_AGENT = 'ST-SoftwareTool'
INSTALL_COMMAND = f'/bin/bash -c "$(curl -fsSL {_INSTALL_SH})"'


def brew_path() -> Optional[str]:
    """Full path of the `brew` command, or None when Homebrew isn't installed."""
    if not IS_MAC:
        return shutil.which('brew')
    for prefix in PREFIXES:
        path = os.path.join(prefix, 'bin', 'brew')
        if os.access(path, os.X_OK):
            return path
    return shutil.which('brew')


def is_installed() -> bool:
    return brew_path() is not None


def version() -> Optional[str]:
    """'Homebrew 4.x.y', or None."""
    brew = brew_path()
    if not brew:
        return None
    try:
        out = subprocess.run([brew, '--version'], capture_output=True, text=True, timeout=20,
                             env={**os.environ, 'HOMEBREW_NO_AUTO_UPDATE': '1'}).stdout
        return out.splitlines()[0].strip() if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def path_dirs() -> list:
    """bin/sbin folders of the Homebrew installs on this Mac (for the terminal's PATH)."""
    dirs = []
    for prefix in PREFIXES:
        for sub in ('bin', 'sbin'):
            d = os.path.join(prefix, sub)
            if os.path.isdir(d):
                dirs.append(d)
    return dirs


def _macos_major() -> int:
    try:
        return int(platform.mac_ver()[0].split('.')[0])
    except (ValueError, IndexError):
        return 0


def can_use_pkg() -> bool:
    """Homebrew.pkg supports Apple Silicon on macOS 15+ only."""
    return IS_MAC and platform.machine() == 'arm64' and _macos_major() >= 15


# ── the .pkg route ───────────────────────────────────────────────────────────

def _latest_pkg() -> Tuple[str, str, str, int]:
    """(version, download url, sha256, size) of the newest Homebrew.pkg."""
    req = urllib.request.Request(_RELEASES_API, headers={
        'User-Agent': _USER_AGENT, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    for asset in data.get('assets') or []:
        name = str(asset.get('name', ''))
        url = str(asset.get('browser_download_url', ''))
        if name.endswith('.pkg') and url.startswith('https://github.com/Homebrew/brew/releases/'):
            algo, _, digest = str(asset.get('digest') or '').partition(':')
            sha = digest.lower() if algo == 'sha256' and re.fullmatch(r'[0-9a-f]{64}', digest.lower()) else ''
            return str(data.get('tag_name', '')), url, sha, int(asset.get('size') or 0)
    raise RuntimeError('The latest Homebrew release has no .pkg installer')


def _download(url: str, dest: str, size: int, sha: str,
              progress: Optional[Callable[[int, int], None]]) -> None:
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, 'wb') as f:
        total = size or int(resp.headers.get('Content-Length') or 0)
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if sha and h.hexdigest() != sha:
        raise RuntimeError('The download does not match Homebrew\'s published checksum')


def verify_pkg(path: str) -> Tuple[bool, str]:
    """Apple notarization + Homebrew's Developer ID signature."""
    try:
        sig = subprocess.run(['/usr/sbin/pkgutil', '--check-signature', path],
                             capture_output=True, text=True, timeout=60).stdout
        gate = subprocess.run(['/usr/sbin/spctl', '-a', '-vv', '-t', 'install', path],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f'could not check the signature ({exc})'
    team = re.search(r'Developer ID Installer: .*\(([A-Z0-9]{10})\)', sig)
    if 'Notarization: trusted by the Apple notary service' not in sig:
        return False, 'the package is not notarized by Apple'
    if not team or team.group(1) not in _PKG_TEAM_IDS:
        return False, 'the package is not signed by Homebrew'
    if gate.returncode != 0 or 'accepted' not in (gate.stdout + gate.stderr):
        return False, 'macOS Gatekeeper rejected the package'
    return True, f'signed by Homebrew ({team.group(1)}), notarized by Apple'


def _run_installer(pkg: str) -> Tuple[bool, str]:
    """Apple's installer with the standard administrator password dialog."""
    quoted = pkg.replace('\\', '\\\\').replace('"', '\\"')
    script = (f'do shell script "/usr/sbin/installer -pkg " & quoted form of "{quoted}" & '
              f'" -target /" with administrator privileges '
              f'with prompt "ST-SoftwareTool wants to install Homebrew."')
    r = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if '-128' in msg:
            return False, 'Cancelled.'
        return False, msg or 'The installer failed.'
    return True, 'Homebrew is installed.'


def install_pkg(progress: Optional[Callable[[str, int, int], None]] = None) -> Tuple[bool, str]:
    """Download, verify and install Homebrew.pkg. Blocking — run it off the UI thread.
    progress(stage, done, total) reports 'download' bytes, then 'verify' and 'install'."""
    step = progress or (lambda *_: None)
    ver, url, sha, size = _latest_pkg()
    tmp = tempfile.mkdtemp(prefix='st-homebrew-')
    pkg = os.path.join(tmp, 'Homebrew.pkg')
    try:
        _download(url, pkg, size, sha, lambda d, t: step('download', d, t))
        step('verify', 0, 0)
        ok, why = verify_pkg(pkg)
        if not ok:
            return False, f'Homebrew {ver} was not installed: {why}.'
        step('install', 0, 0)
        ok, msg = _run_installer(pkg)
        if ok and not is_installed():
            return False, 'The installer finished, but brew was not found in /opt/homebrew.'
        return ok, (f'Homebrew {ver} is installed.' if ok else msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── the Terminal route ───────────────────────────────────────────────────────

def install_in_terminal() -> Tuple[bool, str]:
    """Open Terminal with Homebrew's official install command (Intel / older macOS)."""
    cmd = INSTALL_COMMAND.replace('\\', '\\\\').replace('"', '\\"')
    script = (f'tell application "Terminal"\n activate\n do script "{cmd}"\nend tell')
    r = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or 'Could not open Terminal.').strip()
    return True, ('Terminal opened with Homebrew\'s installer. Follow the steps there '
                  '(it asks for your password).')


def install(progress=None) -> Tuple[bool, str]:
    """Install Homebrew the best way this Mac supports."""
    if not IS_MAC:
        return False, 'Homebrew is installed from ST-SoftwareTool on macOS only.'
    if is_installed():
        return True, f'{version() or "Homebrew"} is already installed.'
    if can_use_pkg():
        return install_pkg(progress)
    return install_in_terminal()
