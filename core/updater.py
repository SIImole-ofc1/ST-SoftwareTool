from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from PySide6.QtWidgets import QMessageBox

APP_VERSION = "1.0.4"
_RELEASES_API = "https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest"


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip("v").split("."))
    except ValueError:
        return (0,)


def check_for_update(parent) -> None:
    """Check GitHub Releases for a newer version and offer one-click install.
    Runs silently on any network error so startup is never blocked."""
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

        # Find the .exe asset in the release
        asset_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".exe"):
                asset_url = asset["browser_download_url"]
                break
        if not asset_url:
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

        installer_path = os.path.join(
            tempfile.gettempdir(), f"ST-Setup-{remote_ver}.exe"
        )
        with urllib.request.urlopen(asset_url, timeout=180) as r:
            with open(installer_path, "wb") as f:
                f.write(r.read())

        subprocess.Popen([installer_path])
        sys.exit(0)

    except Exception:
        pass
