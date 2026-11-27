"""Device Manager › Speed Test: internet speed (ping, jitter, download, upload) and the details
of the current Wi-Fi connection. Results are kept in settings['speedtest_history']."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from core import speedtest
from ui.async_call import call_async

_HISTORY = 10


class _Bridge(QObject):
    progress = Signal(str, float)


class _Reading(QFrame):
    """One big number with a caption."""

    def __init__(self, title: str, unit: str):
        super().__init__()
        self.setObjectName('speedReading')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self._title = QLabel(title)
        self._title.setStyleSheet('font-size: 11px;')
        self._value = QLabel('—')
        f = QFont(self._value.font())
        f.setPointSizeF(f.pointSizeF() * 2.4)
        f.setBold(True)
        self._value.setFont(f)
        self._unit = QLabel(unit)
        self._unit.setStyleSheet('font-size: 11px;')
        for w in (self._title, self._value, self._unit):
            lay.addWidget(w)

    def set(self, value) -> None:
        if value is None:
            self._value.setText('—')
        else:
            self._value.setText(f'{value:.0f}' if value >= 100 else f'{value:.1f}')


class SpeedTestView(QWidget):
    result_ready = Signal(dict)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._running = False
        self._cancel = threading.Event()
        self._bridge = _Bridge(self)
        self._bridge.progress.connect(self._on_progress)
        self._build()
        last = self.history()[:1]
        if last:
            self._show_result(last[0], final=True)
        self._wifi_loaded = False

    def history(self) -> list:
        h = self.manager.settings.get('speedtest_history') or []
        return [r for r in h if isinstance(r, dict)]

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(10)

        top = QHBoxLayout()
        self._ping = _Reading('PING', 'ms')
        self._jitter = _Reading('JITTER', 'ms')
        self._down = _Reading('DOWNLOAD', 'Mbps')
        self._up = _Reading('UPLOAD', 'Mbps')
        for r in (self._ping, self._jitter, self._down, self._up):
            top.addWidget(r, 1)
        root.addLayout(top)

        row = QHBoxLayout()
        self._start = QPushButton('Start Speed Test')
        self._start.setObjectName('primaryButton')
        self._start.setMinimumHeight(34)
        self._start.clicked.connect(self._toggle)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setValue(0)
        self._status = QLabel('Measures your internet connection with Cloudflare\'s speed-test '
                              'servers (about 30 seconds; uses some data).')
        self._status.setWordWrap(True)
        row.addWidget(self._start)
        row.addWidget(self._bar, 1)
        root.addLayout(row)
        root.addWidget(self._status)

        bottom = QHBoxLayout()
        wifi = QGroupBox('Wi-Fi')
        self._wifi_grid = QGridLayout(wifi)
        self._wifi_labels = {}
        for i, (key, title) in enumerate((('name', 'Network'), ('signal', 'Signal'),
                                          ('standard', 'Standard'), ('channel', 'Channel'),
                                          ('link', 'Link speed'), ('security', 'Security'))):
            k = QLabel(title)
            v = QLabel('…')
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._wifi_grid.addWidget(k, i, 0)
            self._wifi_grid.addWidget(v, i, 1)
            self._wifi_labels[key] = v
        self._wifi_grid.setColumnStretch(1, 1)
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self.load_wifi)
        self._wifi_grid.addWidget(refresh, 6, 1, Qt.AlignRight)
        bottom.addWidget(wifi, 1)

        hist = QGroupBox('Recent results')
        hl = QVBoxLayout(hist)
        self._hist = QListWidget()
        hl.addWidget(self._hist)
        bottom.addWidget(hist, 1)
        root.addLayout(bottom, 1)
        self._fill_history()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._wifi_loaded:
            self.load_wifi()

    # ── Wi-Fi ─────────────────────────────────────────────────────────────────

    def load_wifi(self) -> None:
        self._wifi_loaded = True
        for v in self._wifi_labels.values():
            v.setText('…')
        call_async(self, speedtest.wifi_info, self._show_wifi)

    def _show_wifi(self, info) -> None:
        if not isinstance(info, dict) or not info.get('connected'):
            for v in self._wifi_labels.values():
                v.setText('—')
            self._wifi_labels['name'].setText('Not connected to Wi-Fi (or Wi-Fi is off)')
            return
        sig = info.get('signal')
        if sig is not None:
            signal = f"{sig} dBm  ·  {speedtest.signal_quality(sig)}"
            if info.get('noise') is not None:
                signal += f"  (noise {info['noise']} dBm)"
        elif info.get('signal_percent') is not None:
            signal = f"{info['signal_percent']}%"
        else:
            signal = '—'
        name = info.get('name') or ('Hidden by macOS (needs Location permission)'
                                    if speedtest.IS_MAC else '—')
        values = {'name': name, 'signal': signal, 'standard': info.get('standard') or '—',
                  'channel': info.get('channel') or '—',
                  'link': f"{info['link']} Mbps" if info.get('link') else '—',
                  'security': info.get('security') or '—'}
        for k, v in values.items():
            self._wifi_labels[k].setText(str(v))

    # ── the test ──────────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        if self._running:
            self._cancel.set()
            self._start.setEnabled(False)
            self._status.setText('Stopping…')
            return
        self._running = True
        self._cancel = threading.Event()
        self._start.setText('Stop')
        self._bar.setValue(0)
        for r in (self._ping, self._jitter, self._down, self._up):
            r.set(None)
        self._status.setText('Finding the nearest Cloudflare server…')
        self._t0 = time.monotonic()
        cancel = self._cancel
        call_async(self, lambda: speedtest.run(self._bridge.progress.emit, cancel), self._done)

    def _on_progress(self, stage: str, value: float) -> None:
        elapsed = time.monotonic() - self._t0
        if stage == 'server':
            self._status.setText('Measuring ping…')
        elif stage == 'ping':
            self._ping.set(value)
            self._status.setText('Measuring download speed…')
            self._bar.setValue(10)
        elif stage == 'download':
            self._down.set(value)
            self._bar.setValue(min(55, 10 + int(elapsed * 2.5)))
        elif stage == 'upload':
            if self._status.text().startswith('Measuring download'):
                self._status.setText('Measuring upload speed…')
            self._up.set(value)
            self._bar.setValue(min(98, 55 + int((elapsed - 20) * 4.5)))

    def _done(self, result) -> None:
        self._running = False
        self._start.setEnabled(True)
        self._start.setText('Start Speed Test')
        if not isinstance(result, dict) or 'download' not in result or 'upload' not in result:
            self._bar.setValue(0)
            stopped = self._cancel.is_set()
            self._status.setText('Stopped.' if stopped else
                                 f'The test failed — is the internet connected? '
                                 f'({result[1] if isinstance(result, tuple) else result})')
            return
        self._bar.setValue(100)
        self._show_result(result, final=True)
        hist = [result] + self.history()
        self.manager.settings['speedtest_history'] = hist[:_HISTORY]
        try:
            self.manager.save()
        except Exception:
            pass
        self._fill_history()
        self.result_ready.emit(result)

    def _show_result(self, r: dict, final: bool = False) -> None:
        self._ping.set(r.get('ping'))
        self._jitter.set(r.get('jitter'))
        self._down.set(r.get('download'))
        self._up.set(r.get('upload'))
        if final:
            when = datetime.fromtimestamp(r.get('time', time.time())).strftime('%d %b %Y, %H:%M')
            self._status.setText(f"Server: Cloudflare {r.get('server', '')}  ·  tested {when}")

    def _fill_history(self) -> None:
        self._hist.clear()
        for r in self.history():
            when = datetime.fromtimestamp(r.get('time', 0)).strftime('%d %b, %H:%M')
            self._hist.addItem(f"{when}    ↓ {r.get('download', 0):.0f}   ↑ {r.get('upload', 0):.0f} Mbps"
                               f"    ping {r.get('ping', 0):.0f} ms")
        if not self._hist.count():
            self._hist.addItem('No tests yet.')

    def cleanup(self) -> None:
        self._cancel.set()
