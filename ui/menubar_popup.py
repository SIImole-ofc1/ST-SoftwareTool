"""Menu-bar popup (macOS): opens when the ST logo in the menu bar is clicked, like Stats.

Shows live performance, more system details (cores, memory, disk, network, battery, busiest
apps), the VPN state with a connect button, the desktop-widget switch and Open / Quit. Closes
again when the user clicks anywhere else.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.async_call import call_async
from ui.perf_widget import PerfPanel, is_dark
from ui.popup_details import DetailsPanel

_RADIUS = 16


class MenuBarPopup(QWidget):
    open_app       = Signal()
    quit_app       = Signal()
    widget_toggled = Signal(bool)
    vpn_clicked    = Signal()

    def __init__(self, version: str):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.closed_at = 0.0      # when it last closed (a click on the menu-bar icon closes it)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 12)
        root.setSpacing(8)

        self.panel = PerfPanel(self, title='ST-SoftwareTool', framed=False)
        root.addWidget(self.panel)

        # More details, refreshed every 2 s while the popup is open (never in the background)
        self.details = DetailsPanel(self)
        root.addWidget(self.details)
        self._details_timer = QTimer(self)
        from ui import perf_mode
        self._details_timer.setInterval(perf_mode.interval(2000))   # Performance mode: 4 s
        self._details_timer.timeout.connect(self._refresh_details)
        self._details_busy = False

        # VPN (shown once the VPN is available on this Mac)
        self._vpn_row = QWidget()
        vpn = QHBoxLayout(self._vpn_row)
        vpn.setContentsMargins(16, 0, 16, 0)
        self._vpn_dot = QLabel('●')
        self._vpn_lbl = QLabel('VPN  Off')
        self._vpn_btn = QPushButton('Connect')
        self._vpn_btn.clicked.connect(self.vpn_clicked)
        vpn.addWidget(self._vpn_dot)
        vpn.addWidget(self._vpn_lbl)
        vpn.addStretch()
        vpn.addWidget(self._vpn_btn)
        self._vpn_row.setVisible(False)
        root.addWidget(self._vpn_row)

        self._widget_chk = QCheckBox('Performance widget on desktop')
        self._widget_chk.toggled.connect(self.widget_toggled)
        widget_row = QHBoxLayout()
        widget_row.setContentsMargins(16, 0, 16, 0)
        widget_row.addWidget(self._widget_chk)
        root.addLayout(widget_row)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(16, 4, 16, 0)
        self._open_btn = QPushButton('Open ST-SoftwareTool')
        self._quit_btn = QPushButton('Quit')
        self._open_btn.clicked.connect(lambda: (self.hide(), self.open_app.emit()))
        self._quit_btn.clicked.connect(lambda: (self.hide(), self.quit_app.emit()))
        buttons.addWidget(self._open_btn, 1)
        buttons.addWidget(self._quit_btn)
        root.addLayout(buttons)

        self._version = QLabel(f'v{version}')
        self._version.setAlignment(Qt.AlignRight)
        root.addWidget(self._version)

        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._apply_style())
        except AttributeError:
            pass
        self._apply_style()
        self.setFixedWidth(self.panel.width() + 20)

    # ── what's shown (Settings › Widgets & Menu Bar) ───────────────────────────

    def apply_sections(self, settings: dict) -> None:
        keys = settings.get('popup_sections')
        on = (lambda k: True) if not keys else (lambda k: k in keys)
        self.panel.set_visible_parts(on('bars'), on('netline'))
        self.details.set_shown(None if not keys else keys)
        self._vpn_allowed = on('vpn')
        self._vpn_row.setVisible(self._vpn_allowed and self._vpn_available)
        self._widget_chk.setVisible(on('widgets'))
        self.adjustSize()

    # ── state in ──────────────────────────────────────────────────────────────

    _vpn_allowed = True
    _vpn_available = False

    def set_vpn(self, available: bool, connected: bool = False, busy: bool = False) -> None:
        self._vpn_available = available
        self._vpn_row.setVisible(available and self._vpn_allowed)
        self._vpn_lbl.setText('VPN  Connecting…' if busy else
                              'VPN  Connected (Tor)' if connected else 'VPN  Off')
        self._vpn_dot.setStyleSheet(f'color: {"#30D158" if connected else "#FF9F0A" if busy else "#8E8E93"};')
        self._vpn_btn.setText('Disconnect' if connected else 'Connect')
        self._vpn_btn.setEnabled(not busy)
        self.adjustSize()

    def set_widget_checked(self, on: bool) -> None:
        if self._widget_chk.isChecked() != on:
            self._widget_chk.blockSignals(True)
            self._widget_chk.setChecked(on)
            self._widget_chk.blockSignals(False)

    # ── showing ───────────────────────────────────────────────────────────────

    def show_below(self, anchor: QRect) -> None:
        """Open just under the menu-bar icon (or top-right of the screen if unknown)."""
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) if anchor.isValid() else None
        area = (screen or QGuiApplication.primaryScreen()).availableGeometry()
        if anchor.isValid() and not anchor.isEmpty():
            x = anchor.center().x() - self.width() // 2
            y = anchor.bottom() + 6
        else:
            x, y = area.right() - self.width() - 8, area.top() + 6
        x = max(area.left() + 6, min(x, area.right() - self.width() - 6))
        y = max(area.top() + 2, y)
        self.move(QPoint(x, y))
        self.show()
        self.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_details()
        self._details_timer.start()

    def hideEvent(self, event) -> None:
        self.closed_at = time.monotonic()
        self._details_timer.stop()
        super().hideEvent(event)

    def _refresh_details(self) -> None:
        if self._details_busy:
            return
        self._details_busy = True
        from core import sysinfo

        def done(snap):
            self._details_busy = False
            self.details.set_snapshot(snap)
            self.adjustSize()
        call_async(self, sysinfo.snapshot, done)

    # ── look ──────────────────────────────────────────────────────────────────

    def _apply_style(self) -> None:
        dark = is_dark()
        text = '#f5f5f7' if dark else '#1d1d1f'
        sub = 'rgba(235,235,245,0.6)' if dark else 'rgba(60,60,67,0.6)'
        btn = 'rgba(255,255,255,0.12)' if dark else 'rgba(0,0,0,0.06)'
        btn_hover = 'rgba(255,255,255,0.20)' if dark else 'rgba(0,0,0,0.11)'
        self.setStyleSheet(
            f'QLabel, QCheckBox {{ color: {text}; background: transparent; font-size: 12px; }}'
            f'QPushButton {{ color: {text}; background: {btn}; border: none; border-radius: 7px;'
            f'  padding: 5px 12px; font-size: 12px; }}'
            f'QPushButton:hover {{ background: {btn_hover}; }}'
            f'QPushButton:disabled {{ color: {sub}; }}')
        self._version.setStyleSheet(f'color: {sub}; font-size: 10px;')
        self.update()

    def paintEvent(self, _event) -> None:
        dark = is_dark()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS)
        p.fillPath(shape, QColor(30, 30, 32, 240) if dark else QColor(246, 246, 248, 245))
        p.setPen(QColor(255, 255, 255, 36) if dark else QColor(0, 0, 0, 34))
        p.drawPath(shape)
        p.end()
