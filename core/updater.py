from __future__ import annotations

import ctypes
import json
import os
import tempfile
import threading
import urllib.request

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

APP_VERSION = "1.0.20"
_RELEASES_API = "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest"
_USER_AGENT = "ST-SoftwareTool-Updater"

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


def _fetch_latest() -> tuple[str, str]:
    """Return (result, installer_url). result is a version string, _UPTODATE or _ERROR."""
    try:
        req = urllib.request.Request(_RELEASES_API, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return _ERROR, ""

    remote_ver = data.get("tag_name", "").lstrip("v")
    if not remote_ver:
        return _ERROR, ""
    if _ver(remote_ver) <= _ver(APP_VERSION):
        return _UPTODATE, ""

    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".exe"):
            return remote_ver, asset["browser_download_url"]
    return _ERROR, ""


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

    state: dict = {"done": False, "result": "", "url": ""}

    def _worker():
        state["result"], state["url"] = _fetch_latest()
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
                        "Could not reach the update server.\n"
                        "Check your internet connection and try again.")
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
