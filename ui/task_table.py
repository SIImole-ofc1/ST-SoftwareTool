"""
Lightweight MVC table models for the Task Manager tab.

Using QAbstractTableModel + QTableView instead of QTreeWidget means Qt
only calls data() for the ~20 rows that are visible on screen — no Python
objects are created for the 280+ hidden rows, so tab-switch and live
updates are essentially instantaneous.
"""
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
)
from typing import List, Optional

from core.task_manager import ProcessInfo, HistoryEntry


# ── Process table ─────────────────────────────────────────────────────────────

class ProcessTableModel(QAbstractTableModel):
    HEADERS = ["Name", "PID", "CPU %", "RAM %", "RAM MB", "Status", "Started"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._procs: List[ProcessInfo] = []

    # ── required overrides ────────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._procs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._procs):
            return None
        p   = self._procs[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return (
                p.name,
                str(p.pid),
                f"{p.cpu_percent:.1f}%",
                f"{p.ram_percent:.1f}%",
                f"{p.ram_mb:.0f}",
                p.status,
                p.started_str,
            )[col]
        if role == Qt.UserRole:
            return p          # full ProcessInfo for the selection handler
        if role == Qt.UserRole + 1:
            # Raw numeric value used by the sort proxy
            if col == 1: return p.pid
            if col == 2: return p.cpu_percent
            if col == 3: return p.ram_percent
            if col == 4: return p.ram_mb
            if col == 6: return p.create_time_ts
        return None

    # ── data update ───────────────────────────────────────────────────────────

    def set_procs(self, new_procs: List[ProcessInfo]) -> None:
        """Incremental update: remove gone, update changed, append new.

        Preserves scroll position and selection — no full model reset.
        """
        new_by_pid = {p.pid: p for p in new_procs}

        # ── 1. Remove gone processes (high → low so indices stay valid) ──────
        gone_rows = sorted(
            [i for i, p in enumerate(self._procs) if p.pid not in new_by_pid],
            reverse=True,
        )
        for row in gone_rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            self._procs.pop(row)
            self.endRemoveRows()

        # ── 2. Update surviving processes in-place ────────────────────────────
        lo = hi = -1
        for i, p in enumerate(self._procs):
            np = new_by_pid.get(p.pid)
            if np is None:
                continue
            if (p.cpu_percent != np.cpu_percent
                    or p.ram_mb     != np.ram_mb
                    or p.ram_percent != np.ram_percent
                    or p.status     != np.status):
                self._procs[i] = np
                lo = i if lo == -1 else lo
                hi = i
        if lo != -1:
            self.dataChanged.emit(
                self.index(lo, 0),
                self.index(hi, len(self.HEADERS) - 1),
                [Qt.DisplayRole, Qt.UserRole],
            )

        # ── 3. Append brand-new processes ─────────────────────────────────────
        existing = {p.pid for p in self._procs}
        added    = [new_by_pid[pid] for pid in new_by_pid if pid not in existing]
        if added:
            first = len(self._procs)
            self.beginInsertRows(QModelIndex(), first, first + len(added) - 1)
            self._procs.extend(added)
            self.endInsertRows()

    def proc_at(self, row: int) -> Optional[ProcessInfo]:
        return self._procs[row] if 0 <= row < len(self._procs) else None


# ── History table ─────────────────────────────────────────────────────────────

class HistoryTableModel(QAbstractTableModel):
    HEADERS = ["Name", "PID", "Closed At", "Duration"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[HistoryEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        h   = self._entries[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return (h.name, str(h.pid), h.closed_str, h.duration_str)[col]
        return None

    def set_entries(self, entries: List[HistoryEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def clear_all(self) -> None:
        self.beginResetModel()
        self._entries = []
        self.endResetModel()


# ── Combined sort + filter proxy ──────────────────────────────────────────────

class TaskSortProxy(QSortFilterProxyModel):
    """
    Numeric-aware sort for PID / CPU% / RAM% / RAM MB / Started columns.
    Substring name-filter runs without touching the source model at all.
    """

    def filterAcceptsRow(self, source_row: int,
                         source_parent: QModelIndex) -> bool:
        pat = self.filterRegularExpression().pattern()
        if not pat:
            return True
        name = (self.sourceModel()
                    .index(source_row, 0, source_parent)
                    .data(Qt.DisplayRole) or "")
        return pat.lower() in name.lower()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        # Columns with stored numeric UserRole+1
        if col in (1, 2, 3, 4, 6):
            lv = left.data(Qt.UserRole + 1)
            rv = right.data(Qt.UserRole + 1)
            if lv is not None and rv is not None:
                return lv < rv
        ld = left.data(Qt.DisplayRole) or ""
        rd = right.data(Qt.DisplayRole) or ""
        return ld < rd
