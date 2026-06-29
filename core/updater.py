from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import urllib.request

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

APP_VERSION = "1.0.20"
_RELEASES_API = "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest"


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip("v").split("."))
    except ValueError:
        return (0,)


def check_for_update(parent, force: bool = False) -> None:
    """Check GitHub for a newer release.

    force=True  — called from Help menu; always shows a result dialog.
    force=False — silent startup check; only shows dialog when update found.
    """
    state: dict = {"result": None, "url": ""}

    def _worker():
        try:
            req = urllib.request.Request(
                _RELEASES_API,
                headers={"User-Agent": "ST-SoftwareTool-Updater"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            remote_ver: str = data.get("tag_name", "").lstrip("v")
            if not remote_ver:
                if force:
                    state["result"] = "__error__"
                return

            if _ver(remote_ver) <= _ver(APP_VERSION):
                if force:
                    state["result"] = "__uptodate__"
                return

            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".exe"):
                    state["url"] = asset["browser_download_url"]
                    break
            if state["url"]:
                state["result"] = remote_ver
            elif force:
                state["result"] = "__error__"
        except Exception:
            if force:
                state["result"] = "__error__"

    threading.Thread(target=_worker, daemon=True).start()

    def _poll():
        if state["result"] is None:
            QTimer.singleShot(300, _poll)
            return
        try:
            _prompt_and_download(parent, state["result"], state["url"])
        except RuntimeError:
            pass  # parent window was closed before result arrived

    QTimer.singleShot(300, _poll)


def _prompt_and_download(parent, remote_ver: str, asset_url: str) -> None:
    if remote_ver == "__uptodate__":
        QMessageBox.information(parent, "No Updates",
                                f"You are on the latest version (v{APP_VERSION}).")
        return
    if remote_ver == "__error__":
        QMessageBox.warning(parent, "Update Check Failed",
                            "Could not reach the update server.\nCheck your internet connection and try again.")
        return

    reply = QMessageBox.question(
        parent,
        "Update Available",
        f"ST-SoftwareTool v{remote_ver} is available.\n"
        f"You have v{APP_VERSION}.\n\n"
        "Download and install now?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if reply != QMessageBox.Yes:
        return

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

    def _download():
        try:
            with urllib.request.urlopen(asset_url, timeout=180) as r:
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
                            state["pct"] = int(received * 100 / total)
            if not state["cancelled"]:
                state["done"] = True
        except Exception:
            state["error"] = True
            try:
                os.remove(installer_path)
            except OSError:
                pass

    threading.Thread(target=_download, daemon=True).start()

    def _poll():
        if progress.wasCanceled():
            state["cancelled"] = True
            try:
                os.remove(installer_path)
            except OSError:
                pass
            return
        if state["error"]:
            progress.close()
            QMessageBox.warning(parent, "Update Failed",
                                "Could not download the update. Please try again later.")
            return
        if state["done"]:
            progress.setValue(100)
            progress.close()
            subprocess.Popen(
                [installer_path, '/VERYSILENT', '/NORESTART', '/FORCECLOSEAPPLICATIONS'],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            # Exit immediately so Inno Setup won't find us still running
            os._exit(0)
            return
        progress.setValue(state["pct"])
        QTimer.singleShot(250, _poll)

    QTimer.singleShot(250, _poll)
