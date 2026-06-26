from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from PySide6.QtWidgets import QMessageBox

APP_VERSION = "1.0.0"
# Change this to your actual Cloudflare Pages domain after deploying
MANIFEST_URL = "https://st-softwaretool.pages.dev/download/latest.json"


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def check_for_update(parent) -> None:
    """Fetch latest.json and offer to download + install a newer version.
    Runs silently on any network error so startup is never blocked."""
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        remote_ver: str = data.get("version", "")
        if not remote_ver or _ver(remote_ver) <= _ver(APP_VERSION):
            return

        reply = QMessageBox.question(
            parent,
            "Update Available",
            f"ST-SoftwareTool {remote_ver} is available.\n"
            f"You have {APP_VERSION}.\n\n"
            "Download and install now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        url: str = data.get("url", "")
        if not url:
            return

        installer_path = os.path.join(
            tempfile.gettempdir(), f"ST-Setup-{remote_ver}.exe"
        )
        with urllib.request.urlopen(url, timeout=120) as r:
            with open(installer_path, "wb") as f:
                f.write(r.read())

        subprocess.Popen([installer_path])
        sys.exit(0)

    except Exception:
        pass
