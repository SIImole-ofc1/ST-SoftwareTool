from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from core.osinfo import IS_MAC, IS_WINDOWS, mac_app_bundle

APP_VERSION = "1.0.28"
_REPO = "SIImole-ofc1/ST-SoftwareTool"
_RELEASES_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
_LATEST_JSON = "https://st-softwaretool.pages.dev/download/latest.json"  # fallback source
_USER_AGENT = "ST-SoftwareTool-Updater"
_TIMEOUT = 15  # seconds; generous so checks also work through the (slower) Tor VPN
if IS_WINDOWS:
    _LOG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ST-SoftwareTool")
elif IS_MAC:
    _LOG_DIR = os.path.expanduser("~/Library/Application Support/ST-SoftwareTool")
else:
    _LOG_DIR = os.path.expanduser("~/.local/share/ST-SoftwareTool")


def _mac_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def installer_name() -> str:
    """Release asset this computer installs from."""
    if IS_MAC:
        return f"ST-SoftwareTool-macOS-{_mac_arch()}.dmg"
    return "ST-SoftwareTool-Setup.exe"

# How often the running app re-checks for a new release (it can live in the tray for days).
AUTO_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

_UPTODATE = "__uptodate__"
_ERROR = "__error__"

# Session state shared by manual and automatic checks.
_busy = False                  # a check, prompt or download is in progress
_declined: set[str] = set()    # versions the user said "No" to (auto checks stay quiet about them)


_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}")
_DOWNLOAD_DIR_PREFIX = "st-installer-"


class _Asset(NamedTuple):
    """The installer file to download, with what the release says it should be."""
    url: str
    sha256: str = ""  # lowercase hex from GitHub's asset digest; "" when the source gives none
    size: int = 0     # bytes; 0 when unknown


class _VerifyError(Exception):
    """The downloaded file is not the one the release describes."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects (GitHub sends downloads on to its file CDN), but never to plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(newurl, code, "refused a non-HTTPS redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def _trusted_download(url: str) -> bool:
    """Only this repository's own GitHub release file for this computer, over HTTPS."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if (parts.scheme != "https" or parts.netloc.lower() != "github.com"
            or parts.query or parts.fragment):
        return False
    pattern = rf"/{re.escape(_REPO)}/releases/download/v?\d+(?:\.\d+)*/{re.escape(installer_name())}"
    return re.fullmatch(pattern, parts.path, re.IGNORECASE) is not None


def _sha256_from(asset: dict) -> str:
    """GitHub lists each release file's checksum as "digest": "sha256:<hex>"."""
    algo, _, value = str(asset.get("digest") or "").partition(":")
    value = value.strip().lower()
    return value if algo.lower() == "sha256" and re.fullmatch(r"[0-9a-f]{64}", value) else ""


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                               "Cache-Control": "no-cache"})
    with _opener.open(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("unexpected response format")
    return data


def _from_github() -> tuple[str, _Asset | None]:
    data = _get_json(_RELEASES_API)
    ver = str(data.get("tag_name") or "").lstrip("v")
    want = installer_name().lower()
    for a in data.get("assets") or []:
        if isinstance(a, dict) and str(a.get("name", "")).lower() == want:
            size = a.get("size")
            return ver, _Asset(str(a.get("browser_download_url") or ""), _sha256_from(a),
                               size if isinstance(size, int) and size > 0 else 0)
    return ver, None


def _from_website() -> tuple[str, _Asset | None]:
    data = _get_json(_LATEST_JSON)
    ver = str(data.get("version") or "").lstrip("v")
    if IS_WINDOWS:
        url = str(data.get("url") or "")
    else:
        # macOS builds are attached to the same GitHub release under a fixed name
        url = f"https://github.com/{_REPO}/releases/download/v{ver}/{installer_name()}" if ver else ""
    return ver, (_Asset(url) if url else None)  # the website gives no checksum


def _describe(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return "HTTP 403 (server refused the request, often a rate limit)"
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"connection failed: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def _log(msg: str) -> None:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(os.path.join(_LOG_DIR, "update.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  v{APP_VERSION}  {msg}\n")
    except OSError:
        pass


def _fetch_latest() -> tuple[str, _Asset | None, str]:
    """Return (result, installer, error_detail).

    result is a version string, _UPTODATE or _ERROR. Tries the GitHub API first and falls
    back to the website's download/latest.json (no rate limit, works through the VPN too).
    """
    errors = []
    for name, source in (("GitHub", _from_github), ("website", _from_website)):
        try:
            ver, asset = source()
        except Exception as exc:
            errors.append(f"{name}: {_describe(exc)}")
            continue
        if not ver:
            errors.append(f"{name}: no version in response")
            continue
        if not _VERSION_RE.fullmatch(ver):
            errors.append(f"{name}: unexpected version {ver[:40]!r}")
            continue
        if _ver(ver) <= _ver(APP_VERSION):
            return _UPTODATE, None, ""
        if asset and not _trusted_download(asset.url):
            errors.append(f"{name}: refused a download address outside the official releases "
                          f"({asset.url[:120]})")
            continue
        if asset:
            return ver, asset, ""
        errors.append(f"{name}: v{ver} has no {installer_name()} file")
        if not IS_WINDOWS:
            # GitHub answered: this release simply has no build for this Mac yet.
            # (The website only lists versions, so it can't offer a different file.)
            return _UPTODATE, None, ""

    detail = "\n".join(errors)
    _log("update check failed | " + detail.replace("\n", " | "))
    return _ERROR, None, detail


def start_auto_updates(parent) -> None:
    """Check shortly after startup, then every AUTO_CHECK_INTERVAL_MS while the app runs."""
    QTimer.singleShot(3000, lambda: check_for_update(parent))
    timer = QTimer(parent)
    timer.setInterval(AUTO_CHECK_INTERVAL_MS)
    timer.timeout.connect(lambda: check_for_update(parent))
    timer.start()
    parent._update_timer = timer  # keep a reference alive


def check_for_update(parent, force: bool = False) -> None:
    """Check GitHub for a newer release.

    force=True  — from Help > Check for Updates; always reports the result.
    force=False — automatic check; only speaks up when a new version is found.
    """
    global _busy
    if _busy:
        if force:
            QMessageBox.information(parent, "Check for Updates",
                                    "An update check is already in progress.")
        return
    _busy = True

    checking = None
    if force:
        checking = QProgressDialog("Checking for updates...", None, 0, 0, parent)
        checking.setWindowTitle("Check for Updates")
        checking.setWindowModality(Qt.WindowModal)
        checking.setMinimumDuration(0)
        checking.show()

    state: dict = {"done": False, "result": _ERROR, "asset": None, "detail": ""}

    def _worker():
        try:
            state["result"], state["asset"], state["detail"] = _fetch_latest()
        except Exception as exc:  # never leave the poller waiting forever
            state["detail"] = _describe(exc)
        finally:
            state["done"] = True

    threading.Thread(target=_worker, daemon=True).start()

    def _poll():
        global _busy
        if not state["done"]:
            QTimer.singleShot(200, _poll)
            return
        if checking is not None:
            checking.close()
        result, asset = state["result"], state["asset"]
        try:
            if result == _UPTODATE:
                if force:
                    QMessageBox.information(
                        parent, "No Updates",
                        f"ST-SoftwareTool is up to date.\n\nYou have the latest version (v{APP_VERSION}).")
            elif result == _ERROR:
                if force:
                    QMessageBox.warning(
                        parent, "Update Check Failed",
                        "Could not reach the update server.\n\n"
                        f"Details:\n{state['detail'] or 'unknown error'}\n\n"
                        "Check your internet connection (and the VPN, if it is on) "
                        "and try again.")
            elif force or result not in _declined:
                if _prompt_and_download(parent, result, asset):
                    return  # download running; it clears _busy when it ends
        except RuntimeError:
            pass  # parent window was destroyed before the result arrived
        _busy = False

    QTimer.singleShot(200, _poll)


def _prompt_and_download(parent, remote_ver: str, asset: _Asset) -> bool:
    """Ask to update; on Yes start the download. Returns True if a download started."""
    if parent.isHidden():  # running in the tray: bring the window up so the prompt is seen
        parent.showNormal()
        parent.activateWindow()

    reply = QMessageBox.question(
        parent,
        "Update Available",
        f"A new version of ST-SoftwareTool is available.\n\n"
        f"Installed:  v{APP_VERSION}\n"
        f"Latest:       v{remote_ver}\n\n"
        "Update now? The app will close, install the update and restart automatically.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if reply != QMessageBox.Yes:
        _declined.add(remote_ver)
        return False

    _download_and_install(parent, remote_ver, asset)
    return True


def _new_download_dir() -> str:
    """A fresh folder with a unique name (owner-only on macOS) for the installer, so nothing
    can pre-place or swap a file at a known path. Folders left by earlier updates are removed."""
    base = tempfile.gettempdir()
    try:
        for name in os.listdir(base):
            if name.startswith(_DOWNLOAD_DIR_PREFIX):
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    except OSError:
        pass
    return tempfile.mkdtemp(prefix=_DOWNLOAD_DIR_PREFIX)


def _download_and_install(parent, remote_ver: str, asset: _Asset) -> None:
    global _busy
    try:
        installer_path = os.path.join(_new_download_dir(), installer_name())
    except OSError as exc:
        _busy = False
        _log(f"update v{remote_ver}: could not create a download folder: {exc}")
        QMessageBox.warning(parent, "Update Failed",
                            "Could not prepare the update download. Please try again later.")
        return
    if not asset.sha256:
        _log(f"update v{remote_ver}: source gave no checksum; relying on the official address only")

    progress = QProgressDialog(
        f"Downloading ST-SoftwareTool v{remote_ver}...", "Cancel", 0, 100, parent
    )
    progress.setWindowTitle("Downloading Update")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    # error: "" (none), "download" (network/disk problem) or "verify" (file isn't the release's)
    state: dict = {"pct": 0, "done": False, "error": "", "cancelled": False}

    def _remove_installer():
        try:
            os.remove(installer_path)
        except OSError:
            pass

    def _download():
        try:
            req = urllib.request.Request(asset.url, headers={"User-Agent": _USER_AGENT})
            sha = hashlib.sha256()
            with _opener.open(req, timeout=180) as r:
                try:
                    total = int(r.headers.get("Content-Length", 0) or 0)
                except (ValueError, TypeError):
                    total = 0
                received = 0
                with open(installer_path, "wb") as f:
                    while not state["cancelled"]:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        sha.update(chunk)
                        received += len(chunk)
                        if total:
                            state["pct"] = min(99, int(received * 100 / total))
            if state["cancelled"]:
                _remove_installer()
                return
            if total and received < total:
                raise IOError("incomplete download")
            if asset.size and received != asset.size:
                raise _VerifyError(f"{received} bytes, the release lists {asset.size}")
            if asset.sha256 and sha.hexdigest() != asset.sha256:
                raise _VerifyError(f"SHA-256 {sha.hexdigest()} does not match the release's "
                                   f"{asset.sha256}")
            state["done"] = True
        except _VerifyError as exc:
            _log(f"update v{remote_ver} rejected: {exc}")
            state["error"] = "verify"
            _remove_installer()
        except Exception as exc:
            _log(f"update v{remote_ver} download failed: {_describe(exc)}")
            state["error"] = "download"
            _remove_installer()

    threading.Thread(target=_download, daemon=True).start()

    def _poll():
        global _busy
        if progress.wasCanceled():
            state["cancelled"] = True
            _busy = False
            return
        if state["error"]:
            progress.close()
            _busy = False
            if state["error"] == "verify":
                QMessageBox.warning(parent, "Update Failed",
                                    "The downloaded update did not match the official release "
                                    "file, so it was deleted and not installed.\n\n"
                                    "Please try again later, or download it from the website.")
            else:
                QMessageBox.warning(parent, "Update Failed",
                                    "Could not download the update. Please try again later.")
            return
        if state["done"]:
            progress.setValue(100)
            progress.close()
            if _launch_installer(installer_path):
                # Exit right away so the installer can replace our files; it relaunches
                # the app when it finishes (/AUTOUPDATE=1 on Windows, helper script on macOS).
                os._exit(0)
            _busy = False
            QMessageBox.warning(parent, "Update Cancelled",
                                "The update could not be installed automatically.\n"
                                "You can try again from Help > Check for Updates, or download it "
                                "from the website.")
            return
        progress.setValue(state["pct"])
        QTimer.singleShot(250, _poll)

    QTimer.singleShot(250, _poll)


# macOS: wait for the app to quit, replace the .app bundle from the disk image, reopen it.
# If the bundle can't be replaced (no write access), open the disk image for a manual install.
_MAC_INSTALL_SCRIPT = r"""#!/bin/sh
PID="$1"; DMG="$2"; DEST="$3"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
MNT=$(mktemp -d /tmp/st-update.XXXXXX)
if ! hdiutil attach -nobrowse -noverify -noautoopen -mountpoint "$MNT" "$DMG"; then
    open "$DMG"; exit 1
fi
APP=$(ls -d "$MNT"/*.app 2>/dev/null | head -n 1)
OK=0
if [ -n "$APP" ] && [ -n "$DEST" ]; then
    rm -rf "$DEST.old"
    if mv "$DEST" "$DEST.old" 2>/dev/null; then
        if ditto "$APP" "$DEST"; then
            rm -rf "$DEST.old"; OK=1
        else
            rm -rf "$DEST"; mv "$DEST.old" "$DEST"
        fi
    fi
fi
hdiutil detach "$MNT" -quiet
rmdir "$MNT" 2>/dev/null
if [ "$OK" = 1 ]; then
    xattr -dr com.apple.quarantine "$DEST" 2>/dev/null
    rm -f "$DMG"
    open "$DEST"
else
    open "$DMG"
fi
"""


def _launch_installer(path: str) -> bool:
    if IS_MAC:
        return _launch_mac_installer(path)
    if not IS_WINDOWS:
        return False
    return _launch_windows_installer(path)


def _launch_mac_installer(dmg: str) -> bool:
    script = os.path.join(os.path.dirname(dmg), "st-install-update.sh")  # private download folder
    try:
        with open(script, "w") as f:
            f.write(_MAC_INSTALL_SCRIPT)
        os.chmod(script, 0o755)
        subprocess.Popen(["/bin/sh", script, str(os.getpid()), dmg, mac_app_bundle() or ""],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _launch_windows_installer(path: str) -> bool:
    """Start the installer silently. ShellExecute handles the UAC prompt if we aren't elevated."""
    params = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /AUTOUPDATE=1"
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", path, params, None, 0)
    except Exception:
        return False
    return rc > 32  # values <= 32 are errors (e.g. the user declined UAC)
