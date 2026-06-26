"""Hourly background virus scanner with optional auto-blocking."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from core.antivirus_manager import AntivirusManager, ThreatEntry


class _ScanWorker(QThread):
    threat_found   = Signal(object)   # ThreatEntry
    threat_blocked = Signal(object)   # ThreatEntry (auto-blocked)
    finished       = Signal(int, int) # total_threats, auto_blocked

    def __init__(self, mgr: AntivirusManager, auto_block: bool, parent=None):
        super().__init__(parent)
        self._mgr        = mgr
        self._auto_block = auto_block

    def run(self) -> None:
        threats: list[ThreatEntry] = []
        blocked = 0

        def on_threat(t: ThreatEntry) -> None:
            threats.append(t)
            self.threat_found.emit(t)
            if self._auto_block and t.severity == 'red' and t.threat_type != 'green':
                ok, _ = self._mgr.block_threat(t)
                if ok:
                    blocked_ref[0] += 1
                    self.threat_blocked.emit(t)

        blocked_ref = [0]
        try:
            self._mgr.quick_scan(progress_cb=None, threat_cb=on_threat)
        except Exception:
            pass
        self.finished.emit(len(threats), blocked_ref[0])


class BackgroundScanner(QObject):
    """Launches a quick scan every hour; auto-blocks critical threats if configured."""

    scan_started   = Signal()
    scan_done      = Signal(int, int)   # total threats, auto-blocked
    threat_blocked = Signal(object)     # ThreatEntry

    _INTERVAL_MS = 60 * 60 * 1000      # 1 hour

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr        = AntivirusManager()
        self._worker: _ScanWorker | None = None
        self._auto_block = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fire)

    # ── public control ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._timer.start(self._INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()
        if self._worker and self._worker.isRunning():
            self._mgr.stop()
            self._worker.wait(3000)

    def set_auto_block(self, value: bool) -> None:
        self._auto_block = value

    def trigger_now(self) -> None:
        self._fire()

    # ── internal ──────────────────────────────────────────────────────────────

    def _fire(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.scan_started.emit()
        self._worker = _ScanWorker(self._mgr, self._auto_block, self)
        self._worker.threat_blocked.connect(self.threat_blocked)
        self._worker.finished.connect(self.scan_done)
        self._worker.start()
