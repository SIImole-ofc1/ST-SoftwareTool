"""Camera and microphone usage monitor — polls Windows ConsentStore every 5 s."""
from __future__ import annotations

import os
import threading
import winreg
from typing import List

from PySide6.QtCore import QThread, Signal

_CONSENT = (
    r'SOFTWARE\Microsoft\Windows\CurrentVersion'
    r'\CapabilityAccessManager\ConsentStore'
)
_WEBCAM_KEY = _CONSENT + r'\webcam'
_MIC_KEY    = _CONSENT + r'\microphone'


def _is_active(key: winreg.HKEYType) -> bool:
    """Return True if LastUsedTimeStart is set and LastUsedTimeStop is missing or 0."""
    try:
        start, _ = winreg.QueryValueEx(key, 'LastUsedTimeStart')
    except OSError:
        return False
    if not start:
        return False
    try:
        stop, _ = winreg.QueryValueEx(key, 'LastUsedTimeStop')
        return stop == 0
    except OSError:
        # Stop value absent → device still in use
        return True


def _active_apps(consent_key: str) -> List[str]:
    """Return display names of apps currently holding the device open.

    Scans both packaged apps (timestamps directly under their subkey) and
    non-packaged Win32 apps (timestamps one level deeper under NonPackaged).
    """
    names: List[str] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, consent_key)
    except OSError:
        return names

    idx = 0
    while True:
        try:
            sub_name = winreg.EnumKey(root, idx)
        except OSError:
            break
        idx += 1

        try:
            sub = winreg.OpenKey(root, sub_name)
        except OSError:
            continue

        if sub_name == 'NonPackaged':
            # Each child of NonPackaged is a Win32 exe path (# instead of \)
            np_idx = 0
            while True:
                try:
                    app_key_name = winreg.EnumKey(sub, np_idx)
                except OSError:
                    break
                np_idx += 1
                try:
                    app_key = winreg.OpenKey(sub, app_key_name)
                    if _is_active(app_key):
                        exe = app_key_name.replace('#', os.sep)
                        names.append(os.path.basename(exe) or app_key_name[:40])
                    winreg.CloseKey(app_key)
                except OSError:
                    pass
        else:
            # Packaged (MSIX/UWP) app — timestamps live directly here
            if _is_active(sub):
                # e.g. "MSTeams_8wekyb3d8bbwe" → "MSTeams"
                names.append(sub_name.split('_')[0])

        winreg.CloseKey(sub)

    winreg.CloseKey(root)
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
        self._wake       = threading.Event()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def run(self) -> None:
        while not self.isInterruptionRequested():
            if self._enabled:
                self._poll()
            # Interruptible 5-second sleep: wakes immediately on shutdown()
            self._wake.wait(5.0)
            self._wake.clear()

    def _poll(self) -> None:
        # Camera
        cam_apps = _active_apps(_WEBCAM_KEY)
        cam_on   = bool(cam_apps)
        if cam_on and not self._cam_active:
            self.camera_started.emit(', '.join(cam_apps))
        elif not cam_on and self._cam_active:
            self.camera_stopped.emit()
        self._cam_active = cam_on

        # Microphone
        mic_apps = _active_apps(_MIC_KEY)
        mic_on   = bool(mic_apps)
        if mic_on and not self._mic_active:
            self.mic_started.emit(', '.join(mic_apps))
        elif not mic_on and self._mic_active:
            self.mic_stopped.emit()
        self._mic_active = mic_on

    def shutdown(self) -> None:
        self.requestInterruption()
        self._wake.set()   # wake the thread so it exits without waiting 5 s
        self.wait(3000)
