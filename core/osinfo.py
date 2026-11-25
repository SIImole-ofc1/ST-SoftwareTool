"""Operating-system helpers shared by the whole app (Windows, macOS, Linux)."""
from __future__ import annotations

import os
import subprocess
import sys

IS_WINDOWS = sys.platform == 'win32'
IS_MAC     = sys.platform == 'darwin'
IS_LINUX   = sys.platform.startswith('linux')

# Pass as creationflags= to hide console windows. On macOS/Linux creationflags must be 0.
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

OS_NAME = 'Windows' if IS_WINDOWS else 'macOS' if IS_MAC else 'Linux'


def open_path(path: str) -> None:
    """Open a file, folder, app or URL with the system's default handler."""
    if IS_WINDOWS:
        os.startfile(path)
    elif IS_MAC:
        subprocess.Popen(['open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(['xdg-open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def mac_app_bundle() -> str | None:
    """Path of the running ST-SoftwareTool.app bundle (macOS build), else None."""
    if not IS_MAC:
        return None
    path = os.path.realpath(sys.executable)
    marker = '.app/Contents/MacOS/'
    i = path.find(marker)
    return path[:i + 4] if i != -1 else None
