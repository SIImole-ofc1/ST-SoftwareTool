"""Advanced Startup Manager UI widget."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QThread, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
    QTableView, QTextEdit, QVBoxLayout, QWidget,
)

from core.startup_manager import StartupManager, StartupEntry


# ── type → accent colour (dark-theme safe; bright enough at #0a0a0a bg) ──────
_TYPE_COLOR: dict = {
    'Boot Execute':         '#ff4444',   # very early — bright red
    'IFEO Debugger':        '#ff6622',   # potential hijack — orange
    'Winlogon':             '#ffaa00',   # logon hook — amber
    'AppInit DLL':          '#ff8800',   # injected into everything — orange
    'Run Key':              '#00cc44',   # normal startup key — green
    'Startup Folder':       '#44ff88',   # folder shortcut — light green
    'Scheduled Task':       '#4488ff',   # task — blue
    'Shell Extension':      '#aaaaaa',   # context menu — grey
    'Browser Helper Object': '#888888',  # BHO — dim grey
}

_DISABLED_COLOR = '#444444'


# ── background worker ─────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    done  = Signal(list)
    error = Signal(str)

    def __init__(self, mgr: StartupManager, parent=None):
        super().__init__(parent)
        self._mgr = mgr

    def run(self) -> None:
        try:
            self.done.emit(self._mgr.scan_all())
        except Exception as exc:
            self.error.emit(str(exc))


# ── MVC table model ───────────────────────────────────────────────────────────

class StartupTableModel(QAbstractTableModel):
    HEADERS = ['Name', 'Type', 'Enabled', 'Location', 'Command']

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[StartupEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        e   = self._entries[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            cmd = (e.command[:75] + '…') if len(e.command) > 75 else e.command
            return (e.name, e.location_type,
                    'Yes' if e.enabled else 'No',
                    e.location, cmd)[col]
        if role == Qt.UserRole:
            return e
        if role == Qt.ForegroundRole:
            if not e.enabled:
                return QBrush(QColor(_DISABLED_COLOR))
            c = _TYPE_COLOR.get(e.location_type)
            if c:
                return QBrush(QColor(c))
        return None

    def set_entries(self, entries: List[StartupEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entry_at(self, row: int) -> Optional[StartupEntry]:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def notify_changed(self) -> None:
        if self._entries:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._entries) - 1, len(self.HEADERS) - 1),
                [Qt.DisplayRole, Qt.ForegroundRole],
            )


# ── filter + sort proxy ───────────────────────────────────────────────────────

class _StartupProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._type_filter = ''

    def set_type_filter(self, t: str) -> None:
        self._type_filter = t
        self.invalidateFilter()

    def filterAcceptsRow(self, src_row: int, src_parent: QModelIndex) -> bool:
        m = self.sourceModel()
        e = m.entry_at(src_row)
        if e is None:
            return False
        if self._type_filter and e.location_type != self._type_filter:
            return False
        pat = self.filterRegularExpression().pattern().lower()
        if not pat:
            return True
        return (pat in e.name.lower()
                or pat in e.location_type.lower()
                or pat in e.command.lower()
                or pat in e.location.lower())


# ── main widget ───────────────────────────────────────────────────────────────

class StartupView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr    = StartupManager()
        self._worker: Optional[_ScanWorker] = None
        self._loaded = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── filter bar ────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel('Filter:'))

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText('Search name, type, command…')
        self._filter_edit.textChanged.connect(
            lambda t: (self._proxy.setFilterFixedString(t),
                       self._count_lbl.setText(f'Entries: {self._proxy.rowCount()}')))
        top.addWidget(self._filter_edit)

        top.addWidget(QLabel('Type:'))
        self._type_combo = QComboBox()
        self._type_combo.addItem('All Types')
        self._type_combo.currentTextChanged.connect(self._on_type_filter)
        top.addWidget(self._type_combo)

        self._scan_btn = QPushButton('Scan System')
        self._scan_btn.setFixedWidth(100)
        self._scan_btn.clicked.connect(self.scan)
        top.addWidget(self._scan_btn)

        self._status_lbl = QLabel('Not scanned — click  Scan System')
        top.addWidget(self._status_lbl)
        top.addStretch()
        root.addLayout(top)

        # ── legend bar (colour guide) ─────────────────────────────────────────
        legend = QHBoxLayout()
        legend.setSpacing(14)
        for label, color in (
            ('Run Key',        _TYPE_COLOR['Run Key']),
            ('Startup Folder', _TYPE_COLOR['Startup Folder']),
            ('Scheduled Task', _TYPE_COLOR['Scheduled Task']),
            ('Shell Ext.',     _TYPE_COLOR['Shell Extension']),
            ('Winlogon',       _TYPE_COLOR['Winlogon']),
            ('AppInit DLL',    _TYPE_COLOR['AppInit DLL']),
            ('Boot Execute',   _TYPE_COLOR['Boot Execute']),
            ('IFEO Hijack',    _TYPE_COLOR['IFEO Debugger']),
        ):
            dot = QLabel('●')
            dot.setStyleSheet(f'color: {color}; font-size: 12px;')
            lbl = QLabel(label)
            lbl.setStyleSheet('font-size: 10px;')
            legend.addWidget(dot)
            legend.addWidget(lbl)
        legend.addStretch()
        root.addLayout(legend)

        # ── splitter ──────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        left = QGroupBox('Startup Entries')
        ll   = QVBoxLayout(left)
        self._model = StartupTableModel(self)
        self._proxy = _StartupProxy(self)
        self._proxy.setSourceModel(self._model)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col, w in enumerate((200, 140, 55, 220)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            self._table.setColumnWidth(col, w)
        self._table.selectionModel().selectionChanged.connect(self._on_sel)
        ll.addWidget(self._table)
        splitter.addWidget(left)

        right = QGroupBox('Details')
        rl    = QVBoxLayout(right)
        self._detail = QTextEdit(readOnly=True)
        self._detail.setFont(QFont('Consolas', 9))
        rl.addWidget(self._detail)
        right.setFixedWidth(310)
        splitter.addWidget(right)

        root.addWidget(splitter, 1)

        # ── action bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        self._btn_enable  = QPushButton('Enable')
        self._btn_disable = QPushButton('Disable')
        self._count_lbl   = QLabel('Entries: 0')

        for btn in (self._btn_enable, self._btn_disable):
            btn.setEnabled(False)
            bar.addWidget(btn)

        self._btn_enable.clicked.connect(self._enable_entry)
        self._btn_disable.clicked.connect(self._disable_entry)

        note = QLabel(
            'Enable / Disable available for Run Keys and Startup Folder items.'
            '  Other types require manual changes.'
        )
        note.setStyleSheet('color: #666666; font-size: 10px;')
        bar.addStretch()
        bar.addWidget(note)
        bar.addWidget(self._count_lbl)
        root.addLayout(bar)

    # ── scanning ──────────────────────────────────────────────────────────────

    def scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText('Scanning…')
        self._status_lbl.setText('Scanning all startup locations…')
        self._worker = _ScanWorker(self._mgr, self)
        self._worker.done.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def scan_once(self) -> None:
        if not self._loaded:
            self.scan()

    def _on_scan_done(self, entries: list) -> None:
        self._loaded = True
        self._model.set_entries(entries)
        self._proxy.invalidate()
        n = self._proxy.rowCount()
        self._count_lbl.setText(f'Entries: {n}')
        self._status_lbl.setText(f'Found {len(entries)} startup entries')
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText('Scan System')
        # populate type filter combo
        types = sorted({e.location_type for e in entries})
        self._type_combo.blockSignals(True)
        self._type_combo.clear()
        self._type_combo.addItem('All Types')
        for t in types:
            self._type_combo.addItem(t)
        self._type_combo.blockSignals(False)
        self._type_combo.setCurrentIndex(0)

    def _on_scan_error(self, msg: str) -> None:
        self._status_lbl.setText(f'Error: {msg}')
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText('Scan System')

    def cleanup(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)

    # ── selection ─────────────────────────────────────────────────────────────

    def _sel(self) -> Optional[StartupEntry]:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        return self._model.entry_at(self._proxy.mapToSource(idx).row())

    def _on_sel(self) -> None:
        e = self._sel()
        if e:
            self._detail.setPlainText(e.detail_text())
        can_toggle = e is not None and e.location_type in ('Run Key', 'Startup Folder')
        self._btn_enable.setEnabled(can_toggle and not e.enabled if e else False)
        self._btn_disable.setEnabled(can_toggle and e.enabled if e else False)

    def _on_type_filter(self, text: str) -> None:
        self._proxy.set_type_filter('' if text == 'All Types' else text)
        self._count_lbl.setText(f'Entries: {self._proxy.rowCount()}')

    # ── enable / disable ──────────────────────────────────────────────────────

    def _enable_entry(self) -> None:
        e = self._sel()
        if not e:
            return
        ok, msg = self._mgr.enable_entry(e)
        if ok:
            e.enabled = True
            self._model.notify_changed()
            self._on_sel()
        else:
            QMessageBox.warning(self, 'Enable Failed', msg)

    def _disable_entry(self) -> None:
        e = self._sel()
        if not e:
            return
        if QMessageBox.question(
            self, 'Disable Startup Entry',
            f"Disable  '{e.name}'?\n\nIt will no longer run automatically at startup.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        ok, msg = self._mgr.disable_entry(e)
        if ok:
            e.enabled = False
            self._model.notify_changed()
            self._on_sel()
        else:
            QMessageBox.warning(self, 'Disable Failed', msg)
