from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

APP_VERSION = "1.0.9"
_RELEASES_API = "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest"


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip("v").split("."))
    except ValueError:
        return (0,)


class _Signal(QObject):
    found = Signal(str, str)  # remote_ver, asset_url


def check_for_update(parent) -> None:
    sig = _Signal(parent)
    sig.found.connect(lambda ver, url: _prompt_and_download(parent, ver, url))

    def _worker():
        try:
            req = urllib.request.Request(
                _RELEASES_API,
                headers={"User-Agent": "ST-SoftwareTool-Updater"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            remote_ver: str = data.get("tag_name", "").lstrip("v")
            if not remote_ver or _ver(remote_ver) <= _ver(APP_VERSION):
                return

            asset_url = ""
            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".exe"):
                    asset_url = asset["browser_download_url"]
                    break
            if asset_url:
                sig.found.emit(remote_ver, asset_url)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _prompt_and_download(parent, remote_ver: str, asset_url: str) -> None:
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

    threading.Thread(target=_download, daemon=True).start()

    def _poll():
        if progress.wasCanceled():
            state["cancelled"] = True
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
                [installer_path, '/VERYSILENT', '/NORESTART', '/CLOSEAPPLICATIONS'],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            QTimer.singleShot(800, lambda: os._exit(0))
            return
        progress.setValue(state["pct"])
        QTimer.singleShot(250, _poll)

    QTimer.singleShot(250, _poll)
