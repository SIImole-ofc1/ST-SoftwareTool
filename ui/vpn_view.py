"""ST-VPN tab — Tor-based anonymity, no third-party VPN provider needed."""
from __future__ import annotations

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QFrame, QSystemTrayIcon, QMessageBox,
    QSizePolicy, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QPixmap, QFont

from core.vpn_manager import TorVpnManager

_ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets'
)


# ── background workers ────────────────────────────────────────────────────────

class _ConnectWorker(QThread):
    progress = Signal(int, str)    # pct, message
    finished = Signal(bool, str)   # ok, error_msg

    def __init__(self, mgr: TorVpnManager, parent=None):
        super().__init__(parent)
        self._mgr = mgr

    def run(self):
        ok, err = self._mgr.connect(
            progress_cb=lambda pct, msg: self.progress.emit(pct, msg)
        )
        self.finished.emit(ok, err)


class _DownloadWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, mgr: TorVpnManager, parent=None):
        super().__init__(parent)
        self._mgr = mgr

    def run(self):
        ok, err = self._mgr.download_tor(
            progress_cb=lambda pct, msg: self.progress.emit(pct, msg)
        )
        self.finished.emit(ok, err)


class _IpWorker(QThread):
    ready = Signal(str, str)   # ipv4, ipv6

    def __init__(self, mgr: TorVpnManager, parent=None):
        super().__init__(parent)
        self._mgr = mgr

    def run(self):
        v4, v6 = self._mgr.fetch_ips()
        self.ready.emit(v4, v6)


# ── main view ─────────────────────────────────────────────────────────────────

class VpnView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr = TorVpnManager()
        self._real_v4 = ''
        self._real_v6 = ''
        self._worker: _ConnectWorker | None = None
        self._dl_worker: _DownloadWorker | None = None
        self._ip_worker: _IpWorker | None = None
        self._was_connected = False

        self._build_ui()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_stats)
        self._tick.start(1000)

        # Fetch real IP on load
        self._fetch_ip()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)

        # ── Logo ──────────────────────────────────────────────────────────────
        logo_row = QHBoxLayout()
        logo_row.addStretch()
        logo_lbl = QLabel()
        logo_path = os.path.join(_ASSETS, 'STsoftwaretoolVPNLOGO.png.png')
        if os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaledToHeight(90, Qt.SmoothTransformation)
            logo_lbl.setPixmap(pix)
        else:
            logo_lbl.setText('ST  VPN')
            logo_lbl.setFont(QFont('Segoe UI', 22, QFont.Bold))
        logo_row.addWidget(logo_lbl)
        logo_row.addStretch()
        root.addLayout(logo_row)

        # ── Status badge ──────────────────────────────────────────────────────
        self._status_lbl = QLabel('● DISCONNECTED')
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setFont(QFont('Segoe UI', 12, QFont.Bold))
        self._status_lbl.setStyleSheet('color: #ff4444;')
        root.addWidget(self._status_lbl)

        # ── Stealth indicator (auto) ──────────────────────────────────────────
        self._stealth_lbl = QLabel('')
        self._stealth_lbl.setAlignment(Qt.AlignCenter)
        self._stealth_lbl.setStyleSheet('font-size: 9px; color: #00cc33;')
        self._stealth_lbl.setVisible(False)
        root.addWidget(self._stealth_lbl)

        # ── Bootstrap progress bar (hidden when not connecting) ───────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(16)
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        self._progress_lbl = QLabel('')
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        self._progress_lbl.setStyleSheet('font-size: 10px; color: #888888;')
        self._progress_lbl.setVisible(False)
        root.addWidget(self._progress_lbl)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._dl_btn = QPushButton('Download Tor')
        self._dl_btn.setMinimumSize(140, 36)
        self._dl_btn.setToolTip(
            'Downloads the free Tor Expert Bundle from torproject.org\n'
            '(~10 MB, one-time download)'
        )
        self._dl_btn.clicked.connect(self._start_download)
        if self._mgr.is_tor_available():
            self._dl_btn.setVisible(False)
        btn_row.addWidget(self._dl_btn)

        btn_row.addSpacing(10)

        self._id_btn = QPushButton('New Identity')
        self._id_btn.setMinimumSize(130, 36)
        self._id_btn.setEnabled(False)
        self._id_btn.setToolTip(
            'Request a completely new Tor circuit.\n'
            'Your exit IP will change within a few seconds.'
        )
        self._id_btn.clicked.connect(self._new_identity)
        btn_row.addWidget(self._id_btn)

        btn_row.addSpacing(10)

        self._connect_btn = QPushButton('Connect')
        self._connect_btn.setMinimumSize(150, 36)
        self._connect_btn.setFont(QFont('Segoe UI', 10, QFont.Bold))
        self._connect_btn.setEnabled(self._mgr.is_tor_available())
        self._connect_btn.clicked.connect(self._toggle_vpn)
        self._style_connect(False)
        btn_row.addWidget(self._connect_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Kill Switch toggle ────────────────────────────────────────────────
        ks_row = QHBoxLayout()
        ks_row.addStretch()
        self._ks_chk = QCheckBox('Kill Switch')
        self._ks_chk.setToolTip(
            'When the VPN drops unexpectedly, ALL internet traffic is immediately\n'
            'blocked — your real IP cannot leak even if Tor crashes.\n\n'
            'Note: requires administrator privileges to add Windows Firewall rules.'
        )
        self._ks_chk.stateChanged.connect(self._on_ks_toggle)
        ks_row.addWidget(self._ks_chk)
        self._ks_status_lbl = QLabel('  ● BLOCKING ALL TRAFFIC')
        self._ks_status_lbl.setStyleSheet(
            'color: #ff4444; font-size: 9px; font-weight: bold;'
        )
        self._ks_status_lbl.setVisible(False)
        ks_row.addWidget(self._ks_status_lbl)
        ks_row.addStretch()
        root.addLayout(ks_row)

        ks_warn = QLabel('⚠  Kill Switch requires the app to be run as Administrator.')
        ks_warn.setAlignment(Qt.AlignCenter)
        ks_warn.setStyleSheet('color: #ff4444; font-size: 9px;')
        root.addWidget(ks_warn)

        # ── Tor not found hint ────────────────────────────────────────────────
        self._hint_lbl = QLabel(
            'Tor is not installed.  Click "Download Tor" — ST-VPN will handle the rest.'
        )
        self._hint_lbl.setAlignment(Qt.AlignCenter)
        self._hint_lbl.setWordWrap(True)
        self._hint_lbl.setStyleSheet(
            'background:#2a1200; color:#ffaa00; border:1px solid #aa6600;'
            ' border-radius:4px; padding:6px; font-size:10px;'
        )
        self._hint_lbl.setVisible(not self._mgr.is_tor_available())
        root.addWidget(self._hint_lbl)

        # ── Divider ───────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # ── Info panels ───────────────────────────────────────────────────────
        panels = QHBoxLayout()
        panels.setSpacing(12)

        # IP addresses
        ip_box = QGroupBox('IP Addresses')
        ip_l = QVBoxLayout(ip_box)
        ip_l.setSpacing(6)
        self._real_v4_lbl = self._ip_row(ip_l, 'Real IPv4:', '#ff6666')
        self._real_v6_lbl = self._ip_row(ip_l, 'Real IPv6:', '#ff6666')
        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine); ip_l.addWidget(sep2)
        self._tor_v4_lbl  = self._ip_row(ip_l, 'Tor  IPv4:', '#00cc33')
        self._tor_v6_lbl  = self._ip_row(ip_l, 'Tor  IPv6:', '#00cc33')
        refresh_btn = QPushButton('Refresh')
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._fetch_ip)
        ip_l.addWidget(refresh_btn)
        panels.addWidget(ip_box, 3)

        # Tor circuit
        circuit_box = QGroupBox('Tor Circuit  (3-hop onion routing)')
        circ_l = QVBoxLayout(circuit_box)
        circ_l.setSpacing(4)
        self._hop_labels: list[QLabel] = []
        roles = [('Guard  (Entry)', '#4488ff'),
                 ('Middle Relay',   '#ffcc00'),
                 ('Exit Node',      '#00cc33')]
        for role, color in roles:
            row = QHBoxLayout()
            badge = QLabel(role)
            badge.setFixedWidth(110)
            badge.setStyleSheet(f'color:{color}; font-size:9px; font-weight:bold;')
            val = QLabel('—')
            val.setFont(QFont('Consolas', 9))
            val.setWordWrap(True)
            row.addWidget(badge)
            row.addWidget(val, 1)
            circ_l.addLayout(row)
            self._hop_labels.append(val)
        self._circ_note = QLabel(
            'Each hop only knows its neighbours — no single node can trace your full path.'
        )
        self._circ_note.setWordWrap(True)
        self._circ_note.setStyleSheet('font-size: 9px; color: #666666;')
        circ_l.addWidget(self._circ_note)
        panels.addWidget(circuit_box, 4)

        # Stats
        stats_box = QGroupBox('Session Stats')
        st_l = QVBoxLayout(stats_box)
        st_l.setSpacing(6)
        self._dur_lbl   = self._stat_row(st_l, 'Duration:')
        self._sent_lbl  = self._stat_row(st_l, 'Sent:')
        self._recv_lbl  = self._stat_row(st_l, 'Received:')
        self._total_lbl = self._stat_row(st_l, 'Total:')
        panels.addWidget(stats_box, 2)

        root.addLayout(panels, 1)

        # ── How Tor protects you ──────────────────────────────────────────────
        info = QLabel(
            'How ST-VPN protects you:  '
            'Traffic is encrypted in 3 layers and routed through 3 independent servers across the world.  '
            'Your real IP is never seen by the websites you visit.  '
            'Unlike commercial VPNs — no single company holds your data or your keys.'
        )
        info.setWordWrap(True)
        info.setStyleSheet('font-size: 9px; color: #666666; padding: 4px;')
        root.addWidget(info)

    @staticmethod
    def _ip_row(layout, label: str, color: str) -> QLabel:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(80)
        val = QLabel('—')
        val.setFont(QFont('Consolas', 10))
        val.setStyleSheet(f'color:{color};')
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(lbl)
        row.addWidget(val)
        row.addStretch()
        layout.addLayout(row)
        return val

    @staticmethod
    def _stat_row(layout, label: str) -> QLabel:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(75)
        val = QLabel('—')
        val.setFont(QFont('Consolas', 10))
        row.addWidget(lbl)
        row.addWidget(val)
        row.addStretch()
        layout.addLayout(row)
        return val

    # ── connect / disconnect ──────────────────────────────────────────────────

    def _toggle_vpn(self):
        if self._mgr.is_connected():
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        # Remove any active kill switch block so Tor can connect
        if self._mgr.is_ks_active():
            self._mgr.ks_deactivate()
            self._ks_status_lbl.setVisible(False)

        # Auto-select the best available stealth mode (obfs4 > Snowflake > plain Tor)
        stealth_desc = self._mgr.auto_stealth()
        self._stealth_lbl.setText(f'Stealth: {stealth_desc}')
        self._stealth_lbl.setVisible(False)   # shown after connect succeeds

        self._connect_btn.setEnabled(False)
        self._connect_btn.setText('Connecting…')
        self._id_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._progress_lbl.setVisible(True)
        self._status_lbl.setText('● CONNECTING…')
        self._status_lbl.setStyleSheet('color: #ffaa00;')

        self._worker = _ConnectWorker(self._mgr, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_connect_done)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._progress.setValue(pct)
        self._progress_lbl.setText(msg)

    def _on_connect_done(self, ok: bool, err: str):
        self._progress.setVisible(False)
        self._progress_lbl.setVisible(False)
        if not ok:
            self._connect_btn.setEnabled(True)
            self._connect_btn.setText('Connect')
            self._style_connect(False)
            self._status_lbl.setText('● DISCONNECTED')
            self._status_lbl.setStyleSheet('color: #ff4444;')
            QMessageBox.critical(self, 'ST-VPN — Connection Failed', err)
            return

        self._was_connected = True
        self._status_lbl.setText('● CONNECTED  —  Your traffic is anonymized')
        self._status_lbl.setStyleSheet('color: #00cc33; font-weight: bold;')
        self._connect_btn.setText('Disconnect')
        self._connect_btn.setEnabled(True)
        self._style_connect(True)
        self._id_btn.setEnabled(True)
        self._stealth_lbl.setVisible(True)

        # Clear old Tor IPs and fetch new ones
        self._tor_v4_lbl.setText('Fetching…')
        self._tor_v6_lbl.setText('Fetching…')
        QTimer.singleShot(2000, self._fetch_ip)
        # Show circuit after a short delay (circuit needs time to build)
        QTimer.singleShot(3000, self._update_circuit)

    def _do_disconnect(self):
        self._was_connected = False
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText('Disconnecting…')
        self._id_btn.setEnabled(False)

        self._mgr.disconnect()

        self._status_lbl.setText('● DISCONNECTED')
        self._status_lbl.setStyleSheet('color: #ff4444;')
        self._connect_btn.setText('Connect')
        self._connect_btn.setEnabled(True)
        self._style_connect(False)
        self._stealth_lbl.setVisible(False)
        self._ks_status_lbl.setVisible(False)

        # Clear Tor IPs and stats
        self._tor_v4_lbl.setText('—')
        self._tor_v6_lbl.setText('—')
        for lbl in (self._dur_lbl, self._sent_lbl, self._recv_lbl, self._total_lbl):
            lbl.setText('—')
        for lbl in self._hop_labels:
            lbl.setText('—')

        # Refresh real IP (now shows actual IP again)
        QTimer.singleShot(1500, self._fetch_ip)

        self._warn_disconnected()

    def _warn_disconnected(self):
        mw = self.window()
        if hasattr(mw, '_notify'):
            mw._notify(
                'ST-VPN  —  Disconnected!',
                'Your VPN protection is OFF.\n'
                'Your real IP address is now visible to the internet.',
                QSystemTrayIcon.Warning, 7000,
            )
        QMessageBox.warning(
            self, 'ST-VPN Disconnected',
            'ST-VPN has been turned OFF.\n\n'
            'Your real IP address is now visible to websites and services.\n\n'
            'Click Connect to restore your anonymity.',
        )

    # ── new identity ──────────────────────────────────────────────────────────

    def _new_identity(self):
        self._status_lbl.setText('● CONNECTED  —  Requesting new circuit…')
        for lbl in self._hop_labels:
            lbl.setText('Changing…')
        self._tor_v4_lbl.setText('Changing…')
        self._tor_v6_lbl.setText('Changing…')

        ok, msg = self._mgr.new_identity()
        if ok:
            QTimer.singleShot(2000, self._fetch_ip)
            QTimer.singleShot(3000, self._update_circuit)
            self._status_lbl.setText('● CONNECTED  —  New circuit requested')
            QTimer.singleShot(3500, lambda: self._status_lbl.setText(
                '● CONNECTED  —  Your traffic is anonymized'
            ))
        else:
            # Rate-limited or error — show message briefly, stay connected
            self._status_lbl.setText(f'● CONNECTED  —  {msg}')
            for lbl in self._hop_labels:
                lbl.setText('—')
            self._tor_v4_lbl.setText(self._tor_v4_lbl.text().replace('Changing…', '—') or '—')
            self._tor_v6_lbl.setText(self._tor_v6_lbl.text().replace('Changing…', '—') or '—')
            QTimer.singleShot(3000, lambda: self._status_lbl.setText(
                '● CONNECTED  —  Your traffic is anonymized'
            ))

    # ── download Tor ──────────────────────────────────────────────────────────

    def _start_download(self):
        self._dl_btn.setEnabled(False)
        self._dl_btn.setText('Downloading…')
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._progress_lbl.setVisible(True)
        self._progress_lbl.setText('Starting download…')

        self._dl_worker = _DownloadWorker(self._mgr, self)
        self._dl_worker.progress.connect(self._on_progress)
        self._dl_worker.finished.connect(self._on_download_done)
        self._dl_worker.start()

    def _on_download_done(self, ok: bool, err: str):
        self._progress.setVisible(False)
        self._progress_lbl.setVisible(False)
        if ok:
            self._dl_btn.setVisible(False)
            self._hint_lbl.setVisible(False)
            self._connect_btn.setEnabled(True)
            QMessageBox.information(
                self, 'Tor Downloaded',
                'Tor Expert Bundle installed successfully!\n\n'
                'Click  Connect  to start your anonymous session.'
            )
        else:
            self._dl_btn.setEnabled(True)
            self._dl_btn.setText('Download Tor')
            QMessageBox.critical(self, 'Download Failed', err)

    # ── IP fetching ───────────────────────────────────────────────────────────

    def _fetch_ip(self):
        if self._ip_worker and self._ip_worker.isRunning():
            return
        self._ip_worker = _IpWorker(self._mgr, self)
        self._ip_worker.ready.connect(self._on_ip_ready)
        self._ip_worker.start()

    def _on_ip_ready(self, v4: str, v6: str):
        if self._mgr.is_connected():
            # While connected — this IS the Tor IP
            self._tor_v4_lbl.setText(v4 or '(not available)')
            self._tor_v6_lbl.setText(v6 or '(no IPv6 exit)')
            # Show real IP from what we had before connection
            self._real_v4_lbl.setText(self._real_v4 or '(hidden)')
            self._real_v6_lbl.setText(self._real_v6 or '(hidden)')
        else:
            # Not connected — this is the actual real IP
            self._real_v4 = v4
            self._real_v6 = v6
            self._real_v4_lbl.setText(v4 or '(unavailable)')
            self._real_v6_lbl.setText(v6 or '(no IPv6)')
            self._tor_v4_lbl.setText('—')
            self._tor_v6_lbl.setText('—')

    # ── circuit display ───────────────────────────────────────────────────────

    def _update_circuit(self):
        hops = self._mgr.get_circuit()
        if not hops:
            for lbl in self._hop_labels:
                lbl.setText('Building circuit…')
            return
        for i, lbl in enumerate(self._hop_labels):
            if i < len(hops):
                h = hops[i]
                parts = []
                if h.nickname:
                    parts.append(h.nickname)
                if h.country and h.country != '??':
                    parts.append(f'[{h.country}]')
                if h.ip:
                    parts.append(h.ip)
                lbl.setText('  '.join(parts) if parts else '(resolving…)')
            else:
                lbl.setText('—')
        if self._mgr.is_connected():
            self._status_lbl.setText('● CONNECTED  —  Your traffic is anonymized')

    # ── stats tick ────────────────────────────────────────────────────────────

    def _update_stats(self):
        if not self._mgr.is_connected():
            if self._was_connected:
                self._was_connected = False
                if self._mgr.had_unexpected_drop():
                    self._on_unexpected_drop()
            return
        s = self._mgr.get_stats()
        self._dur_lbl.setText(self._mgr.fmt_duration(s.duration_s))
        self._sent_lbl.setText(self._mgr.fmt_bytes(s.bytes_sent))
        self._recv_lbl.setText(self._mgr.fmt_bytes(s.bytes_recv))
        self._total_lbl.setText(self._mgr.fmt_bytes(s.bytes_sent + s.bytes_recv))
        # Refresh circuit display every ~30 s
        if int(s.duration_s) % 30 == 0 and int(s.duration_s) > 0:
            self._update_circuit()

    # ── kill switch ───────────────────────────────────────────────────────────

    def _on_ks_toggle(self, state: int):
        enabled = (state == Qt.Checked)
        self._mgr.set_kill_switch(enabled)
        if not enabled and self._mgr.is_ks_active():
            self._mgr.ks_deactivate()
            self._ks_status_lbl.setVisible(False)
            if not self._mgr.is_connected():
                self._status_lbl.setText('● DISCONNECTED')
                self._status_lbl.setStyleSheet('color: #ff4444;')
                self._connect_btn.setText('Connect')
                self._style_connect(False)

    def _on_unexpected_drop(self):
        """Called from the UI tick when the monitor detects Tor died."""
        self._id_btn.setEnabled(False)
        self._style_connect(False)
        self._stealth_lbl.setVisible(False)

        for lbl in (self._dur_lbl, self._sent_lbl, self._recv_lbl, self._total_lbl):
            lbl.setText('—')
        for lbl in self._hop_labels:
            lbl.setText('—')
        self._tor_v4_lbl.setText('—')
        self._tor_v6_lbl.setText('—')

        ks_on      = self._ks_chk.isChecked()
        ks_active  = self._mgr.is_ks_active()

        self._connect_btn.setText('Reconnect')
        self._connect_btn.setEnabled(True)

        if ks_active:
            self._status_lbl.setText('● KILL SWITCH ACTIVE — All traffic blocked')
            self._status_lbl.setStyleSheet('color: #ff4444; font-weight: bold;')
            self._ks_status_lbl.setVisible(True)
            msg = (
                'ST-VPN dropped unexpectedly.\n\n'
                'Kill Switch is ACTIVE — all internet traffic is blocked '
                'to protect your real IP from leaking.\n\n'
                'Click  Reconnect  to restore VPN protection, or uncheck '
                'Kill Switch to allow normal traffic.'
            )
            mw = self.window()
            if hasattr(mw, '_notify'):
                mw._notify(
                    'ST-VPN — Kill Switch Active!',
                    'VPN dropped. ALL traffic is now BLOCKED.',
                    QSystemTrayIcon.Critical, 10000,
                )
            QMessageBox.warning(self, 'ST-VPN — Kill Switch Active', msg)
        elif ks_on:
            # Kill switch was checked but firewall rule failed (likely no admin rights)
            self._status_lbl.setText('● CONNECTION LOST  —  Kill switch needs admin rights')
            self._status_lbl.setStyleSheet('color: #ff4444;')
            msg = (
                'ST-VPN dropped unexpectedly.\n\n'
                'Kill switch could not activate — administrator privileges are '
                'required to add Windows Firewall rules.\n\n'
                'Your real IP may be exposed. Click Reconnect to restore protection.'
            )
            mw = self.window()
            if hasattr(mw, '_notify'):
                mw._notify(
                    'ST-VPN — Connection Lost',
                    'VPN dropped. Kill switch failed — run as administrator.',
                    QSystemTrayIcon.Critical, 10000,
                )
            QMessageBox.warning(self, 'ST-VPN — Connection Lost', msg)
        else:
            self._status_lbl.setText('● CONNECTION LOST')
            self._status_lbl.setStyleSheet('color: #ff4444;')
            mw = self.window()
            if hasattr(mw, '_notify'):
                mw._notify(
                    'ST-VPN — Connection Lost',
                    'VPN dropped unexpectedly. Click Reconnect.',
                    QSystemTrayIcon.Warning, 7000,
                )

        QTimer.singleShot(1500, self._fetch_ip)

    # ── style helpers ─────────────────────────────────────────────────────────

    def _style_connect(self, connected: bool):
        if connected:
            self._connect_btn.setStyleSheet(
                'QPushButton{background:#5c1a1a;color:#fff;border:2px solid #cc3333;'
                'border-radius:4px;padding:4px 12px;}'
                'QPushButton:hover{background:#6b2323;}'
                'QPushButton:disabled{background:#1a1a1a;color:#555;border-color:#333;}'
            )
        else:
            self._connect_btn.setStyleSheet(
                'QPushButton{background:#1a5c1a;color:#fff;border:2px solid #00cc33;'
                'border-radius:4px;padding:4px 12px;}'
                'QPushButton:hover{background:#236b23;}'
                'QPushButton:disabled{background:#1a1a1a;color:#555;border-color:#333;}'
            )

    def request_auto_connect(self) -> None:
        """Called by main window when auto_vpn_startup setting is on."""
        if self._mgr.is_tor_available() and not self._mgr.is_connected():
            self._do_connect()

    # ── cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self):
        self._tick.stop()
        for w in (self._worker, self._dl_worker, self._ip_worker):
            if w and w.isRunning():
                w.wait(1000)
        if self._mgr.is_connected():
            self._mgr.disconnect()
