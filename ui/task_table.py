"""
Lightweight MVC table models for the Task Manager tab.

Using QAbstractTableModel + QTableView means Qt only calls data() for the
~20 rows visible on screen — no Python objects are created for hidden rows.
"""
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
)
from typing import List, Optional

from core.task_manager import ProcessInfo, HistoryEntry

# Minimum change required before a row is marked dirty and repainted.
# Avoids constant flicker for idle processes whose values drift by <0.1%.
_CPU_THRESHOLD = 0.2    # CPU %
_RAM_THRESHOLD = 0.5    # RAM MB


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
        """Incremental update — preserves scroll position and selection.

        Only emits dataChanged for rows whose values crossed the change
        threshold, avoiding constant full-table repaints for idle processes.
        """
        new_by_pid = {p.pid: p for p in new_procs}
        _roles     = [Qt.DisplayRole, Qt.UserRole]

        # ── 1. Remove gone processes (high → low so indices stay valid) ──────
        gone_rows = sorted(
            [i for i, p in enumerate(self._procs) if p.pid not in new_by_pid],
            reverse=True,
        )
        for row in gone_rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            self._procs.pop(row)
            self.endRemoveRows()

        # ── 2. Update surviving rows — one signal per changed row ─────────────
        for i, p in enumerate(self._procs):
            np = new_by_pid.get(p.pid)
            if np is None:
                continue
            if (abs(p.cpu_percent - np.cpu_percent) > _CPU_THRESHOLD
                    or abs(p.ram_mb - np.ram_mb) > _RAM_THRESHOLD
                    or p.ram_percent != np.ram_percent
                    or p.status != np.status):
                self._procs[i] = np
                top_left     = self.index(i, 0)
                bottom_right = self.index(i, len(self.HEADERS) - 1)
                self.dataChanged.emit(top_left, bottom_right, _roles)

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
        if len(entries) == len(self._entries):
            return   # nothing closed since last refresh — skip reset
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
