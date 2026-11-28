"""AntiVirus tab UI — smart scan results with live blocking."""
from __future__ import annotations

import html
import os
from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QSplitter, QTableView,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.av_scanner import AntivirusManager, ThreatEntry

_SEV_FG = {
    'red':    '#ff3333',
    'orange': '#ff8800',
    'green':  '#00cc44',
}
_BLOCKED_FG = '#555555'


# ── background scan worker ────────────────────────────────────────────────────

class _ScanWorker(QThread):
    progress     = Signal(str)
    threat_found = Signal(object)   # ThreatEntry
    finished     = Signal(int)      # threat count
    error        = Signal(str)

    def __init__(self, mgr: AntivirusManager, mode: str, parent=None):
        super().__init__(parent)
        self._mgr  = mgr
        self._mode = mode

    def run(self) -> None:
        try:
            threats = (
                self._mgr.quick_scan(self.progress.emit, self.threat_found.emit)
                if self._mode == 'quick'
                else self._mgr.full_scan(self.progress.emit, self.threat_found.emit)
            )
            self.finished.emit(len(threats))
        except Exception as exc:
            self.error.emit(str(exc))


# ── MVC table model ───────────────────────────────────────────────────────────

class _ThreatModel(QAbstractTableModel):
    HEADERS = ['Severity', 'Category', 'Path', 'Reason']

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[ThreatEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        e   = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            path   = ('…' + e.path[-74:]) if len(e.path) > 75 else e.path
            reason = (e.reason[:94] + '…')  if len(e.reason) > 95 else e.reason
            return (e.severity_label, e.category, path, reason)[col]

        if role == Qt.ForegroundRole:
            if e.blocked:
                return QBrush(QColor(_BLOCKED_FG))
            return QBrush(QColor(_SEV_FG.get(e.severity, '#aaaaaa')))

        if role == Qt.FontRole and e.blocked:
            f = QFont()
            f.setStrikeOut(True)
            return f

        if role == Qt.UserRole:
            return e

        if role == Qt.TextAlignmentRole and col == 0:
            return Qt.AlignCenter

        return None

    def add_entry(self, e: ThreatEntry) -> None:
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(e)
        self.endInsertRows()

    def notify_row(self, row: int) -> None:
        self.dataChanged.emit(
            self.index(row, 0),
            self.index(row, len(self.HEADERS) - 1),
            [Qt.DisplayRole, Qt.ForegroundRole, Qt.FontRole],
        )

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def entry_at(self, row: int) -> Optional[ThreatEntry]:
        return self._rows[row] if 0 <= row < len(self._rows) else None


# ── main widget ───────────────────────────────────────────────────────────────

class AntivirusView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr         = AntivirusManager()
        self._worker: Optional[_ScanWorker] = None
        self._current_row = -1
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── header ────────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(14)

        logo_lbl = QLabel()
        from core import vault
        px = vault.pixmap('STsoftwaretoolantivirusLOGO.png.png')
        if True:
            if not px.isNull():
                logo_lbl.setPixmap(
                    px.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        header.addWidget(logo_lbl)

        title_col = QVBoxLayout()
        title_lbl = QLabel('ST-AntiVirus')
        title_lbl.setFont(QFont('Segoe UI', 16, QFont.Bold))
        sub_lbl = QLabel(
            'Scans file contents with a malware hash registry, built-in signatures and your '
            'installed antivirus engine, plus heuristics, running programs, startup entries, '
            'scheduled tasks, services and the hosts file'
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet('color: #888888; font-size: 10px;')
        title_col.addWidget(title_lbl)
        title_col.addWidget(sub_lbl)
        title_col.addStretch()
        header.addLayout(title_col)
        header.addStretch()

        btn_col = QVBoxLayout()
        btn_col.setSpacing(5)
        self._btn_quick = QPushButton('Quick Scan')
        self._btn_full  = QPushButton('Full Scan')
        self._btn_stop  = QPushButton('Stop')
        self._btn_stop.setEnabled(False)
        for b in (self._btn_quick, self._btn_full, self._btn_stop):
            b.setFixedWidth(120)
            btn_col.addWidget(b)
        self._btn_quick.clicked.connect(lambda: self._start_scan('quick'))
        self._btn_full.clicked.connect(lambda: self._start_scan('full'))
        self._btn_stop.clicked.connect(self._stop_scan)
        header.addLayout(btn_col)

        root.addLayout(header)

        # ── status row ────────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._status_lbl = QLabel('Ready — choose Quick Scan or Full Scan to begin')
        self._status_lbl.setStyleSheet('color: #888888; font-size: 10px;')
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        self._count_lbl = QLabel('Threats: 0')
        status_row.addWidget(self._count_lbl)
        root.addLayout(status_row)

        self._stats_lbl = QLabel('')
        self._stats_lbl.setStyleSheet('color: #888888; font-size: 10px;')
        root.addWidget(self._stats_lbl)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._refresh_stats)

        # ── legend ────────────────────────────────────────────────────────────
        legend = QHBoxLayout()
        legend.setSpacing(20)
        for label, color in (
            ('CRITICAL — block immediately',         '#ff3333'),
            ('WARNING — suspicious, review first',   '#ff8800'),
            ('CLEAN — scan completed with no threats','#00cc44'),
        ):
            dot = QLabel('●')
            dot.setStyleSheet(f'color: {color}; font-size: 13px;')
            txt = QLabel(label)
            txt.setStyleSheet(f'color: {color}; font-size: 10px;')
            legend.addWidget(dot)
            legend.addWidget(txt)
        legend.addStretch()
        root.addLayout(legend)

        # ── splitter: threat table  |  detail + block panel ──────────────────
        splitter = QSplitter(Qt.Horizontal)

        left = QGroupBox('Scan Results')
        ll   = QVBoxLayout(left)
        self._model = _ThreatModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col, w in enumerate((80, 200, 300)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            self._table.setColumnWidth(col, w)
        self._table.selectionModel().selectionChanged.connect(self._on_sel)
        ll.addWidget(self._table)
        splitter.addWidget(left)

        # Right panel: detail text + block button
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        detail_box = QGroupBox('Threat Details')
        dl = QVBoxLayout(detail_box)
        self._detail = QTextEdit(readOnly=True)
        self._detail.setFont(QFont('Consolas', 9))
        dl.addWidget(self._detail)
        right_layout.addWidget(detail_box, 1)

        action_box = QGroupBox('Action')
        al = QVBoxLayout(action_box)
        al.setSpacing(6)

        self._btn_block = QPushButton('Block Threat')
        self._btn_block.setEnabled(False)
        self._btn_block.setStyleSheet(
            'QPushButton { background-color: #8b0000; color: #ffffff; font-weight: bold; padding: 6px; }'
            'QPushButton:hover { background-color: #cc0000; }'
            'QPushButton:disabled { background-color: #333333; color: #666666; }'
        )
        self._btn_block.clicked.connect(self._block_selected)
        al.addWidget(self._btn_block)

        self._block_note = QLabel('')
        self._block_note.setStyleSheet('color: #888888; font-size: 10px;')
        self._block_note.setWordWrap(True)
        al.addWidget(self._block_note)

        right_layout.addWidget(action_box)
        right_widget.setFixedWidth(360)
        splitter.addWidget(right_widget)

        root.addWidget(splitter, 1)

    # ── scan control ──────────────────────────────────────────────────────────

    def _start_scan(self, mode: str) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._model.clear()
        self._detail.clear()
        self._block_note.setText('')
        self._btn_block.setEnabled(False)
        self._btn_block.setText('Block Threat')
        self._current_row = -1
        self._btn_quick.setEnabled(False)
        self._btn_full.setEnabled(False)
        self._btn_stop.setEnabled(True)
        label = 'Quick' if mode == 'quick' else 'Full'
        self._status_lbl.setText(f'{label} scan in progress…')
        self._count_lbl.setText('Threats: 0')
        self._stats_lbl.setText('')
        self._stats_timer.start()

        self._worker = _ScanWorker(self._mgr, mode, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.threat_found.connect(self._on_threat)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _stop_scan(self) -> None:
        self._mgr.stop()
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText('Stopping scan…')

    def _on_progress(self, msg: str) -> None:
        if len(msg) > 100:
            msg = msg[:97] + '…'
        self._status_lbl.setText(msg)

    def _on_threat(self, threat: ThreatEntry) -> None:
        self._model.add_entry(threat)
        self._count_lbl.setText(f'Threats: {self._model.rowCount()}')
        self._table.scrollToBottom()

    def _stats_text(self) -> str:
        st = self._mgr.stats
        mins, secs = divmod(int(st.elapsed), 60)
        return (f'Files found: {st.files_seen:,}   ·   analysed: {st.files_checked:,}   ·   '
                f'unchanged since last scan: {st.files_cached:,}   ·   '
                f'online lookups: {st.cloud_lookups:,}   ·   time: {mins}:{secs:02d}')

    def _refresh_stats(self) -> None:
        self._stats_lbl.setText(self._stats_text())

    def _engine_summary(self) -> str:
        st = self._mgr.stats
        av = ', '.join(f'{n} ({"on" if on else "off"})' for n, on, _ in st.av_products) \
            or 'none found'
        hashes = 'online' if st.cloud_ok else 'OFFLINE - file hashes were not checked'
        return (f'Malware hash registry: {hashes}\n'
                f'Built-in signatures: active\n'
                f'Windows AMSI engine: {"available" if st.amsi_ok else "not available"}\n'
                f'Installed antivirus: {av}')

    def _on_done(self, n_threats: int) -> None:
        self._stats_timer.stop()
        self._refresh_stats()
        self._btn_quick.setEnabled(True)
        self._btn_full.setEnabled(True)
        self._btn_stop.setEnabled(False)
        st = self._mgr.stats
        word = 'stopped' if st.stopped else 'complete'
        if n_threats == 0:
            self._model.add_entry(ThreatEntry(
                severity='green',
                category=f'Scan {word.title()}',
                path='All scanned locations',
                reason='No threats detected' + (' (scan was stopped early)' if st.stopped
                                                else ' - your system appears clean'),
                detail=(
                    f'{self._stats_text()}\n\n'
                    'Checked: running programs, startup entries, scheduled tasks, services, '
                    'the hosts file and the contents of the scanned files.\n\n'
                    f'{self._engine_summary()}'
                ),
                threat_type='green',
            ))
            self._status_lbl.setText(f'Scan {word} - no threats found.')
        else:
            confirmed = sum(1 for i in range(self._model.rowCount())
                            if (e := self._model.entry_at(i)) and e.confirmed)
            self._status_lbl.setText(
                f'Scan {word} - {n_threats} finding(s), {confirmed} confirmed malware.  '
                f'Select a row to see details.'
            )
        self._count_lbl.setText(f'Threats: {n_threats}')

    def _on_error(self, msg: str) -> None:
        self._stats_timer.stop()
        self._btn_quick.setEnabled(True)
        self._btn_full.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_lbl.setText(f'Error during scan: {msg}')

    # ── selection ─────────────────────────────────────────────────────────────

    def _on_sel(self) -> None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            self._btn_block.setEnabled(False)
            return
        row = idx.row()
        self._current_row = row
        e = self._model.entry_at(row)
        if e is None:
            return

        # Update detail pane
        color = _SEV_FG.get(e.severity, '#ffffff')
        status_line = (
            f'<span style="color: {_BLOCKED_FG}; font-size: 11pt;">'
            f'✓ Blocked / Quarantined</span>'
            if e.blocked else
            f'<span style="color: {color}; font-size: 12pt; font-weight: bold;">'
            f'■ {e.severity_label} — {e.category}</span>'
        )
        esc = html.escape
        if e.severity == 'green':
            basis = ''
        elif e.confirmed:
            basis = '<b>Detection:</b>\nConfirmed by a malware detection engine\n\n'
        else:
            basis = ('<b>Detection:</b>\nHeuristic (suspicious, not a confirmed virus) - '
                     'review before blocking\n\n')
        self._detail.setHtml(
            f'<pre style="font-family: Consolas; font-size: 9pt; white-space: pre-wrap;">'
            f'{status_line}\n\n'
            f'{basis}'
            f'<b>Path / Location:</b>\n{esc(e.path)}\n\n'
            f'<b>Why flagged:</b>\n{esc(e.reason)}\n\n'
            f'<b>Detail:</b>\n{esc(e.detail or "No additional information.")}'
            f'</pre>'
        )

        # Update block button
        self._btn_block.setEnabled(e.blockable)
        if e.blocked:
            self._btn_block.setText('Already Blocked')
            self._block_note.setText('')
        elif e.threat_type == 'info':
            self._btn_block.setText('Nothing to Block')
            self._block_note.setText('This is a warning about your system settings - '
                                     'follow the steps in the details to fix it.')
        elif e.threat_type == 'green' or e.severity == 'green':
            self._btn_block.setText('Nothing to Block')
            self._block_note.setText('')
        else:
            self._btn_block.setText(e.block_label())
            notes = {
                'file':     'Move the file to the quarantine folder so it cannot execute.',
                'process':  'Terminate the running process immediately.',
                'registry': 'Delete the malicious registry entry.',
                'task':     'Disable the scheduled task so it no longer runs.',
                'service':  'Stop and permanently disable the Windows service.',
            }
            self._block_note.setText(notes.get(e.threat_type, ''))

    # ── blocking ──────────────────────────────────────────────────────────────

    def _block_selected(self) -> None:
        if self._current_row < 0:
            return
        e = self._model.entry_at(self._current_row)
        if e is None or e.blocked:
            return

        # Confirmation dialog
        confirm_text = {
            'file':     f'Quarantine file:\n{e.file_path or e.path}\n\n'
                        f'The file will be moved to the ST quarantine folder and '
                        f'will no longer be able to execute.',
            'process':  f'Terminate process:\n{e.path}\n\n'
                        f'The process will be killed immediately. '
                        f'Save any related work before continuing.',
            'registry': f'Remove registry entry:\n{e.path}\n\n'
                        f'This will delete the registry value. '
                        f'The associated program will no longer run at startup.',
            'task':     f'Disable scheduled task:\n{e.task_name}\n\n'
                        f'The task will be disabled and will no longer run automatically.',
            'service':  f'Stop and disable service:\n{e.svc_name}\n\n'
                        f'The service will be stopped and set to Disabled.',
        }.get(e.threat_type,
              f'Block threat:\n{e.path}\n\nAre you sure?')

        if QMessageBox.warning(
            self, f'Confirm — {e.block_label()}',
            confirm_text,
            QMessageBox.Yes | QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return

        self._btn_block.setEnabled(False)
        self._btn_block.setText('Blocking…')

        ok, msg = self._mgr.block_threat(e)

        if ok:
            self._model.notify_row(self._current_row)
            self._btn_block.setText('Blocked ✓')
            self._block_note.setText(msg)
            self._status_lbl.setText(f'Blocked: {msg}')
            # Refresh detail pane to show blocked state
            self._on_sel()
        else:
            self._btn_block.setEnabled(True)
            self._btn_block.setText(e.block_label())
            self._block_note.setText(f'Failed: {msg}')
            QMessageBox.critical(
                self, 'Block Failed',
                f'{msg}\n\n'
                f'Tip: Try running ST-SoftwareTool as Administrator for elevated access.',
            )

    # ── cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        self._stats_timer.stop()
        if self._worker and self._worker.isRunning():
            self._mgr.stop()
            self._worker.wait(3000)
