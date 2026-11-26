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


def own_executable() -> str:
    """Real path of the running program file: the app binary in a packaged build, the Python
    interpreter in development. Asks the OS, because sys.executable isn't reliable for this
    in every packaging mode."""
    try:
        import psutil
        return os.path.realpath(psutil.Process().exe())
    except Exception:
        return os.path.realpath(sys.executable)


def mac_app_bundle() -> str | None:
    """Path of the running ST-SoftwareTool.app bundle (macOS build), else None."""
    if not IS_MAC:
        return None
    marker = '.app/Contents/MacOS/'
    for path in (own_executable(), os.path.realpath(sys.executable)):
        i = path.find(marker)
        # Only our own app binary counts: from source, the interpreter itself runs inside
        # Python.app, which must never be mistaken for (and "updated" as) this app.
        if i != -1 and os.path.basename(path).startswith('ST-SoftwareTool'):
            return path[:i + 4]
    return None
