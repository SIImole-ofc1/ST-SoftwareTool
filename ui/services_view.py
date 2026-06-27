"""Windows Services Manager UI widget."""
from __future__ import annotations

import subprocess
from typing import List, Optional

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QThread, Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QTableView,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.services_manager import ServicesManager, WindowsService


# ── background workers ────────────────────────────────────────────────────────

class _LoadWorker(QThread):
    done  = Signal(list)
    error = Signal(str)

    def __init__(self, mgr: ServicesManager, parent=None):
        super().__init__(parent)
        self._mgr = mgr

    def run(self):
        try:
            self.done.emit(self._mgr.get_all())
        except Exception as exc:
            self.error.emit(str(exc))


class _ActionWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, args: list, parent=None):
        super().__init__(parent)
        self._args = args

    def run(self):
        try:
            p = subprocess.run(
                ['sc'] + self._args,
                capture_output=True, text=True, timeout=30,
            )
            ok  = p.returncode == 0
            msg = (p.stderr or p.stdout).strip()
            self.done.emit(ok, msg)
        except Exception as exc:
            self.done.emit(False, str(exc))


class _RestartWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name

    def run(self):
        import time
        p = subprocess.run(['sc', 'stop', self._name],
                           capture_output=True, text=True, timeout=30)
        time.sleep(1)
        p2 = subprocess.run(['sc', 'start', self._name],
                            capture_output=True, text=True, timeout=30)
        ok  = p2.returncode == 0
        msg = (p2.stderr or p2.stdout).strip()
        self.done.emit(ok, msg)


# ── MVC table model ───────────────────────────────────────────────────────────

class ServicesTableModel(QAbstractTableModel):
    HEADERS = ['Display Name', 'Service Name', 'State', 'Start Mode', 'PID', 'Account']

    # Colours matched to theme-agnostic hues — main_window.qss tints the rest
    _STATE_COLORS = {
        'Running':        '#00cc33',
        'StartPending':   '#ffcc00',
        'StopPending':    '#ffcc00',
        'PausePending':   '#ffcc00',
        'ContinuePending': '#ffcc00',
        'Paused':         '#ffaa00',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._svcs: List[WindowsService] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._svcs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._svcs):
            return None
        s   = self._svcs[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            pid = str(s.pid) if s.pid else '—'
            return (s.display_name, s.name, s.state,
                    s.start_mode, pid, s.account)[col]
        if role == Qt.UserRole:
            return s
        if role == Qt.ForegroundRole:
            c = self._STATE_COLORS.get(s.state)
            if c:
                return QBrush(QColor(c))
            if s.start_mode == 'Disabled':
                return QBrush(QColor('#555555'))
        return None

    def set_services(self, svcs: List[WindowsService]) -> None:
        self.beginResetModel()
        self._svcs = svcs
        self.endResetModel()

    def svc_at(self, row: int) -> Optional[WindowsService]:
        return self._svcs[row] if 0 <= row < len(self._svcs) else None

    def notify_changed(self) -> None:
        if self._svcs:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._svcs) - 1, len(self.HEADERS) - 1),
                [Qt.DisplayRole, Qt.ForegroundRole],
            )


# ── filter + sort proxy ───────────────────────────────────────────────────────

class _ServiceProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state_filter = ''

    def set_state_filter(self, state: str) -> None:
        self._state_filter = state
        self.invalidateFilter()

    def filterAcceptsRow(self, src_row: int, src_parent: QModelIndex) -> bool:
        m   = self.sourceModel()
        svc = m.svc_at(src_row)
        if svc is None:
            return False
        if self._state_filter and svc.state != self._state_filter:
            return False
        pat = self.filterRegularExpression().pattern().lower()
        if not pat:
            return True
        return pat in svc.display_name.lower() or pat in svc.name.lower()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        if col == 4:  # PID — numeric
            lv = left.data(Qt.DisplayRole) or '0'
            rv = right.data(Qt.DisplayRole) or '0'
            try:
                return int(lv.replace('—', '0')) < int(rv.replace('—', '0'))
            except ValueError:
                pass
        return (left.data(Qt.DisplayRole) or '') < (right.data(Qt.DisplayRole) or '')


# ── Set Startup Type dialog ───────────────────────────────────────────────────

class _StartupTypeDialog(QDialog):
    _MODES = [
        ('Automatic',                 'auto'),
        ('Automatic  (Delayed Start)', 'delayed-auto'),
        ('Manual',                    'demand'),
        ('Disabled',                  'disabled'),
    ]

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Set Startup Type')
        self.setFixedSize(300, 130)
        layout = QVBoxLayout(self)
        form   = QFormLayout()
        self._combo = QComboBox()
        for label, _ in self._MODES:
            self._combo.addItem(label)
        cur = current.lower().replace(' ', '').replace('-', '')
        for i, (_, val) in enumerate(self._MODES):
            if val.replace('-', '') in cur:
                self._combo.setCurrentIndex(i)
                break
        form.addRow('Startup type:', self._combo)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_mode(self) -> str:
        return self._MODES[self._combo.currentIndex()][1]


# ── main widget ───────────────────────────────────────────────────────────────

class ServicesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr           = ServicesManager()
        self._load_worker:   Optional[_LoadWorker]   = None
        self._action_worker: Optional[_ActionWorker] = None
        self._loaded        = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ── filter bar ────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel('Filter:'))

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText('Filter by name…')
        self._filter_edit.textChanged.connect(
            lambda t: (self._proxy.setFilterFixedString(t),
                       self._count_lbl.setText(f'Services: {self._proxy.rowCount()}')))
        top.addWidget(self._filter_edit)

        top.addWidget(QLabel('State:'))
        self._state_combo = QComboBox()
        for s in ('All', 'Running', 'Stopped', 'Paused'):
            self._state_combo.addItem(s)
        self._state_combo.currentTextChanged.connect(self._on_state_filter)
        top.addWidget(self._state_combo)

        self._refresh_btn = QPushButton('Load / Refresh')
        self._refresh_btn.clicked.connect(self.load)
        top.addWidget(self._refresh_btn)

        self._status_lbl = QLabel('Not loaded — click  Load / Refresh')
        top.addWidget(self._status_lbl)
        top.addStretch()
        root.addLayout(top)

        # ── splitter ──────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        left  = QGroupBox('Services')
        ll    = QVBoxLayout(left)
        self._model = ServicesTableModel(self)
        self._proxy = _ServiceProxy(self)
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
        for col, w in enumerate((220, 160, 90, 100, 55)):
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
        self._btn_start   = QPushButton('Start')
        self._btn_stop    = QPushButton('Stop')
        self._btn_restart = QPushButton('Restart')
        self._btn_startup = QPushButton('Set Startup Type…')
        self._count_lbl   = QLabel('Services: 0')

        for btn in (self._btn_start, self._btn_stop,
                    self._btn_restart, self._btn_startup):
            btn.setEnabled(False)
            bar.addWidget(btn)

        self._btn_start.clicked.connect(self._start)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_restart.clicked.connect(self._restart)
        self._btn_startup.clicked.connect(self._set_startup)

        note = QLabel('Some actions require Administrator privileges.')
        note.setStyleSheet('color: #666666; font-size: 10px;')
        bar.addStretch()
        bar.addWidget(note)
        bar.addWidget(self._count_lbl)
        root.addLayout(bar)

    # ── load ──────────────────────────────────────────────────────────────────

    def load(self) -> None:
        if self._load_worker and self._load_worker.isRunning():
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText('Loading…')
        self._status_lbl.setText('Querying Windows Services (WMI)…')
        self._load_worker = _LoadWorker(self._mgr, self)
        self._load_worker.done.connect(self._on_loaded)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

    def load_once(self) -> None:
        if not self._loaded:
            self.load()

    def _on_loaded(self, svcs: list) -> None:
        self._loaded = True
        self._model.set_services(svcs)
        self._proxy.invalidate()
        n = self._proxy.rowCount()
        self._count_lbl.setText(f'Services: {n}')
        self._status_lbl.setText(f'Loaded {len(svcs)} services')
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText('Load / Refresh')

    def _on_load_error(self, msg: str) -> None:
        self._status_lbl.setText(f'Error: {msg}')
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText('Load / Refresh')

    def cleanup(self) -> None:
        for w in (self._load_worker, self._action_worker):
            if w and w.isRunning():
                w.requestInterruption()
                w.wait(65000)

    # ── selection ─────────────────────────────────────────────────────────────

    def _sel(self) -> Optional[WindowsService]:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        return self._model.svc_at(self._proxy.mapToSource(idx).row())

    def _on_sel(self) -> None:
        svc = self._sel()
        if svc:
            self._detail.setPlainText(svc.detail_text())
        self._refresh_action_btns()

    def _on_state_filter(self, text: str) -> None:
        self._proxy.set_state_filter('' if text == 'All' else text)
        self._count_lbl.setText(f'Services: {self._proxy.rowCount()}')

    def _refresh_action_btns(self) -> None:
        busy    = bool(self._action_worker and self._action_worker.isRunning())
        svc     = self._sel()
        running = svc is not None and svc.is_running
        self._btn_start.setEnabled(not busy and svc is not None and not running)
        self._btn_stop.setEnabled(not busy and svc is not None and running)
        self._btn_restart.setEnabled(not busy and svc is not None and running)
        self._btn_startup.setEnabled(not busy and svc is not None)

    # ── service actions ───────────────────────────────────────────────────────

    def _lock_btns(self) -> None:
        for b in (self._btn_start, self._btn_stop,
                  self._btn_restart, self._btn_startup):
            b.setEnabled(False)

    def _start(self) -> None:
        svc = self._sel()
        if not svc:
            return
        self._lock_btns()
        w = _ActionWorker(['start', svc.name], self)
        w.done.connect(lambda ok, msg: self._on_ctrl_done(ok, msg, svc, 'Running', 'Start'))
        self._action_worker = w
        w.start()

    def _stop(self) -> None:
        svc = self._sel()
        if not svc:
            return
        if QMessageBox.warning(
            self, 'Stop Service',
            f"Stop  '{svc.display_name}'?\n\nThis may affect system stability.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self._lock_btns()
        w = _ActionWorker(['stop', svc.name], self)
        w.done.connect(lambda ok, msg: self._on_ctrl_done(ok, msg, svc, 'Stopped', 'Stop'))
        self._action_worker = w
        w.start()

    def _restart(self) -> None:
        svc = self._sel()
        if not svc:
            return
        self._lock_btns()
        w = _RestartWorker(svc.name, self)
        w.done.connect(lambda ok, msg: self._on_ctrl_done(ok, msg, svc, 'Running', 'Restart'))
        self._action_worker = w
        w.start()

    def _set_startup(self) -> None:
        svc = self._sel()
        if not svc:
            return
        dlg = _StartupTypeDialog(svc.start_mode, self)
        if dlg.exec() != QDialog.Accepted:
            return
        mode = dlg.selected_mode()
        self._lock_btns()
        w = _ActionWorker(['config', svc.name, 'start=', mode], self)
        _labels = {'auto': 'Auto', 'delayed-auto': 'Delayed Auto',
                   'demand': 'Manual', 'disabled': 'Disabled'}
        def _done(ok: bool, msg: str) -> None:
            if ok:
                svc.start_mode = _labels.get(mode, mode)
                self._model.notify_changed()
                self._detail.setPlainText(svc.detail_text())
            else:
                self._svc_error('Set Startup Type', msg)
            self._refresh_action_btns()
        w.done.connect(_done)
        self._action_worker = w
        w.start()

    def _on_ctrl_done(self, ok: bool, msg: str,
                      svc: WindowsService, new_state: str, action: str) -> None:
        if ok:
            svc.state = new_state
            if new_state == 'Stopped':
                svc.pid = 0
            self._model.notify_changed()
            self._detail.setPlainText(svc.detail_text())
        else:
            self._svc_error(action, msg)
        self._refresh_action_btns()

    @staticmethod
    def _svc_error(action: str, msg: str) -> None:
        text = msg or '(no error message)'
        if any(k in text.lower() for k in (' 5:', 'access denied', 'error 5')):
            text += '\n\nTry running ST-SoftwareTool as Administrator.'
        QMessageBox.critical(None, f'{action} Failed', text)
