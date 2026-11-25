from __future__ import annotations

import ctypes
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

APP_VERSION = "1.0.22"
_RELEASES_API = "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest"
_LATEST_JSON = "https://st-softwaretool.pages.dev/download/latest.json"  # fallback source
_USER_AGENT = "ST-SoftwareTool-Updater"
_TIMEOUT = 15  # seconds; generous so checks also work through the (slower) Tor VPN
_LOG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ST-SoftwareTool")

# How often the running app re-checks for a new release (it can live in the tray for days).
AUTO_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

_UPTODATE = "__uptodate__"
_ERROR = "__error__"

# Session state shared by manual and automatic checks.
_busy = False                  # a check, prompt or download is in progress
_declined: set[str] = set()    # versions the user said "No" to (auto checks stay quiet about them)


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT,
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def _from_github() -> tuple[str, str]:
    data = _get_json(_RELEASES_API)
    ver = data.get("tag_name", "").lstrip("v")
    url = next((a["browser_download_url"] for a in data.get("assets", [])
                if a.get("name", "").lower().endswith(".exe")), "")
    return ver, url


def _from_website() -> tuple[str, str]:
    data = _get_json(_LATEST_JSON)
    return str(data.get("version", "")).lstrip("v"), data.get("url", "")


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


def _fetch_latest() -> tuple[str, str, str]:
    """Return (result, installer_url, error_detail).

    result is a version string, _UPTODATE or _ERROR. Tries the GitHub API first and falls
    back to the website's download/latest.json (no rate limit, works through the VPN too).
    """
    errors = []
    for name, source in (("GitHub", _from_github), ("website", _from_website)):
        try:
            ver, url = source()
        except Exception as exc:
            errors.append(f"{name}: {_describe(exc)}")
            continue
        if not ver:
            errors.append(f"{name}: no version in response")
            continue
        if _ver(ver) <= _ver(APP_VERSION):
            return _UPTODATE, "", ""
        if url:
            return ver, url, ""
        errors.append(f"{name}: v{ver} has no installer file")

    detail = "\n".join(errors)
    _log("update check failed | " + detail.replace("\n", " | "))
    return _ERROR, "", detail


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

    state: dict = {"done": False, "result": _ERROR, "url": "", "detail": ""}

    def _worker():
        try:
            state["result"], state["url"], state["detail"] = _fetch_latest()
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
        result, url = state["result"], state["url"]
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
                if _prompt_and_download(parent, result, url):
                    return  # download running; it clears _busy when it ends
        except RuntimeError:
            pass  # parent window was destroyed before the result arrived
        _busy = False

    QTimer.singleShot(200, _poll)


def _prompt_and_download(parent, remote_ver: str, asset_url: str) -> bool:
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

    _download_and_install(parent, remote_ver, asset_url)
    return True


def _download_and_install(parent, remote_ver: str, asset_url: str) -> None:
    installer_path = os.path.join(tempfile.gettempdir(), "ST-SoftwareTool-Setup.exe")

    progress = QProgressDialog(
        f"Downloading ST-SoftwareTool v{remote_ver}...", "Cancel", 0, 100, parent
    )
    progress.setWindowTitle("Downloading Update")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    state: dict = {"pct": 0, "done": False, "error": False, "cancelled": False}

    def _remove_installer():
        try:
            os.remove(installer_path)
        except OSError:
            pass

    def _download():
        try:
            req = urllib.request.Request(asset_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as r:
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
                        received += len(chunk)
                        if total:
                            state["pct"] = min(99, int(received * 100 / total))
            if state["cancelled"]:
                _remove_installer()
            elif total and received < total:
                raise IOError("incomplete download")
            else:
                state["done"] = True
        except Exception:
            state["error"] = True
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
            QMessageBox.warning(parent, "Update Failed",
                                "Could not download the update. Please try again later.")
            return
        if state["done"]:
            progress.setValue(100)
            progress.close()
            if _launch_installer(installer_path):
                # Exit right away so the installer can replace our files.
                # /AUTOUPDATE=1 tells the installer to relaunch the app when it finishes.
                os._exit(0)
            _busy = False
            QMessageBox.warning(parent, "Update Cancelled",
                                "The installer could not be started (administrator permission "
                                "is required).\nYou can try again from Help > Check for Updates.")
            return
        progress.setValue(state["pct"])
        QTimer.singleShot(250, _poll)

    QTimer.singleShot(250, _poll)


def _launch_installer(path: str) -> bool:
    """Start the installer silently. ShellExecute handles the UAC prompt if we aren't elevated."""
    params = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /AUTOUPDATE=1"
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", path, params, None, 0)
    except Exception:
        return False
    return rc > 32  # values <= 32 are errors (e.g. the user declined UAC)
