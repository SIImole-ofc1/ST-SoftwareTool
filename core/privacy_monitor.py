"""Camera and microphone usage monitor — polls Windows registry every 5 s."""
from __future__ import annotations

import os
import winreg
from typing import List

from PySide6.QtCore import QThread, Signal

_CONSENT = (
    r'SOFTWARE\Microsoft\Windows\CurrentVersion'
    r'\CapabilityAccessManager\ConsentStore'
)
_WEBCAM_NONPKG = _CONSENT + r'\webcam\NonPackaged'
_MIC_NONPKG    = _CONSENT + r'\microphone\NonPackaged'


def _active_apps(non_pkg_key: str) -> List[str]:
    """Return display names of apps currently holding the device open."""
    names: List[str] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, non_pkg_key)
        idx = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, idx)
                sub = winreg.OpenKey(root, sub_name)
                try:
                    start, _ = winreg.QueryValueEx(sub, 'LastUsedTimeStart')
                    try:
                        stop, _ = winreg.QueryValueEx(sub, 'LastUsedTimeStop')
                    except OSError:
                        stop = 0
                    if start and stop == 0:
                        # '#' replaces '\' in the key name
                        exe = sub_name.replace('#', '\\')
                        names.append(os.path.basename(exe) or sub_name[:40])
                except OSError:
                    pass
                winreg.CloseKey(sub)
                idx += 1
            except OSError:
                break
        winreg.CloseKey(root)
    except OSError:
        pass
    return names


class PrivacyMonitor(QThread):
    """Background thread that fires signals whenever camera/mic state changes."""

    camera_started = Signal(str)   # comma-separated app names
    camera_stopped = Signal()
    mic_started    = Signal(str)
    mic_stopped    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled    = True
        self._cam_active = False
        self._mic_active = False

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self._enabled:
                self._poll()
            self.msleep(5000)

    def _poll(self) -> None:
        # Camera
        cam_apps = _active_apps(_WEBCAM_NONPKG)
        cam_on   = bool(cam_apps)
        if cam_on and not self._cam_active:
            self.camera_started.emit(', '.join(cam_apps))
        elif not cam_on and self._cam_active:
            self.camera_stopped.emit()
        self._cam_active = cam_on

        # Microphone
        mic_apps = _active_apps(_MIC_NONPKG)
        mic_on   = bool(mic_apps)
        if mic_on and not self._mic_active:
            self.mic_started.emit(', '.join(mic_apps))
        elif not mic_on and self._mic_active:
            self.mic_stopped.emit()
        self._mic_active = mic_on

    def shutdown(self) -> None:
        self.requestInterruption()
        self.wait(2000)
