"""File Manager tab — Finder on macOS, File Explorer on Windows, inside ST-SoftwareTool.

Sidebar (favourite folders and drives), back / forward / up, an editable path, list or icon
view (sortable, folders first), a details pane with preview, search (filter as you type,
Enter searches every subfolder), and the usual file actions: open, Quick Look (macOS),
copy / cut / paste (also to and from Finder / Explorer), duplicate, rename, new folder,
move to Trash / Recycle Bin, copy path, show in Finder / Explorer, open in the ST terminal.

Deleting always goes to the Trash / Recycle Bin, never straight away, and asks first.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import (QDir, QFileInfo, QMimeData, QModelIndex, QSize, QSortFilterProxyModel,
                            QStorageInfo, Qt, QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QFont, QGuiApplication, QImageReader, QKeySequence, QPixmap,
                           QShortcut, QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QFileIconProvider, QFileSystemModel, QFrame,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QSplitter, QStackedWidget, QToolButton, QTreeView,
                               QVBoxLayout, QWidget)

from core.osinfo import IS_MAC, IS_WINDOWS, open_path
from ui.async_call import call_async

TRASH = 'Trash' if not IS_WINDOWS else 'Recycle Bin'
FINDER = 'Finder' if IS_MAC else 'File Explorer' if IS_WINDOWS else 'Files'
_MAX_RESULTS = 3000


def _fmt_size(n: Optional[int]) -> str:
    if n is None:
        return '—'
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if n < 1000 or unit == 'TB':
            return f'{n} bytes' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1000.0      # Finder and Explorer's "size on disk" readouts use 1000 on macOS
    return ''


def _unique(folder: str, name: str) -> str:
    """A free name in folder: 'name copy.ext' / 'name copy 2.ext' (macOS) or
    'name - Copy (2).ext' (Windows) — like Finder / Explorer."""
    base, ext = os.path.splitext(name)
    if os.path.isdir(os.path.join(folder, name)):
        base, ext = name, ''
    candidate = os.path.join(folder, name)
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        if IS_WINDOWS:
            suffix = ' - Copy' if n == 1 else f' - Copy ({n})'
        else:
            suffix = ' copy' if n == 1 else f' copy {n}'
        candidate = os.path.join(folder, f'{base}{suffix}{ext}')
        if not os.path.exists(candidate):
            return candidate
        n += 1


_mime_db = None


def _kind(info: QFileInfo, icons: QFileIconProvider) -> str:
    """'Plain Text Document', 'Portable Network Graphics' … rather than 'text/plain'."""
    global _mime_db
    kind = icons.type(info)
    if '/' in kind or not kind:
        from PySide6.QtCore import QMimeDatabase
        _mime_db = _mime_db or QMimeDatabase()
        comment = _mime_db.mimeTypeForFile(info, QMimeDatabase.MatchExtension).comment()
        kind = comment or kind
    if '/' in kind:                       # still a raw type: name it like Finder does
        kind = ('Plain Text Document' if kind == 'text/plain' else
                f'{info.suffix().upper()} File' if info.suffix() else 'Document')
    return kind[:1].upper() + kind[1:] if kind else ''


def _reveal(path: str) -> None:
    if IS_MAC:
        subprocess.Popen(['/usr/bin/open', '-R', path])
    elif IS_WINDOWS:
        # Explorer only understands this exact form: /select,"C:\path\file"
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    else:
        open_path(os.path.dirname(path))


def _is_bundle(path: str) -> bool:
    """macOS apps / packages are folders that Finder opens as one thing."""
    return IS_MAC and os.path.isdir(path) and os.path.splitext(path)[1].lower() in (
        '.app', '.bundle', '.pkg', '.mpkg', '.photoslibrary', '.pages', '.numbers', '.key',
        '.playground', '.xcodeproj', '.xcworkspace', '.rtfd')


def favourites() -> List[tuple]:
    """The user's main folders, where the system really keeps them (e.g. OneDrive on Windows)."""
    from PySide6.QtCore import QStandardPaths as SP
    names = [('Home', SP.HomeLocation), ('Desktop', SP.DesktopLocation),
             ('Documents', SP.DocumentsLocation), ('Downloads', SP.DownloadLocation),
             ('Pictures', SP.PicturesLocation), ('Music', SP.MusicLocation),
             ('Movies' if IS_MAC else 'Videos', SP.MoviesLocation)]
    out = []
    for label, loc in names:
        path = os.path.normpath(SP.writableLocation(loc) or '')
        if path and os.path.isdir(path) and all(path != p for _, p in out):
            out.append((label, path))
    if IS_MAC and os.path.isdir('/Applications'):
        out.insert(1, ('Applications', '/Applications'))
    return out


def locations() -> List[tuple]:
    """Disks and drives: (label, root path)."""
    out = []
    for vol in QStorageInfo.mountedVolumes():
        if not vol.isValid() or not vol.isReady():
            continue
        root = vol.rootPath()
        if IS_MAC and (root.startswith('/System/Volumes') or root.startswith('/private')
                       or root.startswith('/Library/Developer')):
            continue
        if not IS_WINDOWS and not IS_MAC and root.startswith(('/snap', '/boot', '/run/', '/sys', '/proc')):
            continue
        label = vol.displayName() or root
        if IS_WINDOWS:
            label = f'{vol.displayName() or "Local Disk"} ({root[:2]})'
        out.append((label, root))
    return out


# ── models ───────────────────────────────────────────────────────────────────

class _FolderProxy(QSortFilterProxyModel):
    """Filters only the shown folder's items by name, and sorts folders first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_path = ''
        self.text = ''
        self._icons = QFileIconProvider()
        self._kinds: dict = {}

    def data(self, index, role=Qt.DisplayRole):
        # Kind: the names Finder / Explorer show ("Plain Text Document", not "text/plain")
        if role == Qt.DisplayRole and index.column() == 2:
            src = self.mapToSource(index)
            m = self.sourceModel()
            if not m.isDir(src) or _is_bundle(m.filePath(src)):
                info = m.fileInfo(src)
                key = info.suffix().lower() or info.fileName()
                kind = self._kinds.get(key)
                if kind is None:
                    kind = _kind(info, self._icons)
                    self._kinds[key] = kind
                return kind
        return super().data(index, role)

    def set_filter(self, text: str) -> None:
        self.text = text.casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        if not self.text:
            return True
        model = self.sourceModel()
        if model.filePath(parent) != self.root_path:
            return True                      # the folders above the shown one
        return self.text in model.fileName(model.index(row, 0, parent)).casefold()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        m = self.sourceModel()
        ld, rd = m.isDir(left) and not _is_bundle(m.filePath(left)), \
            m.isDir(right) and not _is_bundle(m.filePath(right))
        ascending = self.sortOrder() == Qt.AscendingOrder
        if ld != rd:                         # folders first, whichever way it's sorted
            return ld if ascending else rd
        col = left.column()
        if col == 1:
            a, b = m.size(left), m.size(right)
        elif col == 2:
            a, b = m.type(left).casefold(), m.type(right).casefold()
        elif col == 3:
            a, b = m.lastModified(left), m.lastModified(right)
        else:
            a, b = m.fileName(left).casefold(), m.fileName(right).casefold()
        if a == b:
            return m.fileName(left).casefold() < m.fileName(right).casefold()
        return a < b


class _SearchWorker(QThread):
    """Walks a folder tree for names containing the text."""
    found = Signal(list)        # [(name, folder, is_dir, mtime)]
    done = Signal(int, bool)    # total, stopped early

    def __init__(self, root: str, text: str, hidden: bool, parent=None):
        super().__init__(parent)
        self._root, self._text, self._hidden = root, text.casefold(), hidden

    def run(self) -> None:
        total, batch = 0, []
        stack = [self._root]
        while stack and not self.isInterruptionRequested():
            folder = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:
                if not self._hidden and e.name.startswith('.'):
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if self._text in e.name.casefold():
                    try:
                        mtime = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        mtime = 0
                    batch.append((e.name, folder, is_dir, mtime))
                    total += 1
                    if len(batch) >= 50:
                        self.found.emit(batch)
                        batch = []
                    if total >= _MAX_RESULTS:
                        self.found.emit(batch)
                        self.done.emit(total, True)
                        return
                if is_dir and not _is_bundle(e.path):
                    stack.append(e.path)
        if batch:
            self.found.emit(batch)
        self.done.emit(total, self.isInterruptionRequested())


# ── the view ─────────────────────────────────────────────────────────────────

class FileManagerView(QWidget):
    open_in_terminal = Signal(str)      # a folder to open in the ST terminal

    def __init__(self, parent=None, start: Optional[str] = None):
        super().__init__(parent)
        self._back: List[str] = []
        self._forward: List[str] = []
        self._path = ''
        self._clip: List[str] = []
        self._clip_cut = False
        self._busy = False
        self._search: Optional[_SearchWorker] = None
        self._icons = QFileIconProvider()

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)                  # file actions go through this view
        self.model.setRootPath('')
        self._set_hidden(False)
        self.proxy = _FolderProxy(self)
        self.proxy.setSourceModel(self.model)

        self._build()
        self.go(start or os.path.expanduser('~'))
        self._volume_timer = QTimer(self)
        self._volume_timer.setInterval(5000)
        self._volume_timer.timeout.connect(self._refresh_sidebar)

    # ── construction ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        # toolbar
        bar = QHBoxLayout()
        bar.setSpacing(4)

        def tool(text, tip, slot):
            b = QToolButton()
            b.setText(text)
            b.setObjectName('fmNavButton')
            f = b.font()
            f.setPointSizeF(f.pointSizeF() + 3)
            b.setFont(f)
            b.setFixedWidth(30)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            bar.addWidget(b)
            return b
        mod = 'Cmd' if IS_MAC else 'Alt'
        self._btn_back = tool('←', 'Back (⌘[)' if IS_MAC else 'Back (Alt+Left)', self.back)
        self._btn_fwd = tool('→', 'Forward (⌘])' if IS_MAC else 'Forward (Alt+Right)', self.forward)
        self._btn_up = tool('↑', 'Enclosing folder (⌘↑)' if IS_MAC else 'Up (Alt+Up)', self.up)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText('Folder path — type one and press Enter')
        self.path_edit.returnPressed.connect(lambda: self.go(self.path_edit.text()))
        bar.addWidget(self.path_edit, 1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('Search  (Enter: all subfolders)')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(230)
        self.search_edit.textChanged.connect(self._on_search_text)
        self.search_edit.returnPressed.connect(self._search_subfolders)
        bar.addWidget(self.search_edit)
        self._btn_list = QPushButton('List')
        self._btn_icons = QPushButton('Icons')
        for b, mode in ((self._btn_list, 0), (self._btn_icons, 1)):
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=mode: self._set_view(m))
            bar.addWidget(b)
        self._btn_list.setChecked(True)
        more = QToolButton()
        more.setObjectName('fmNavButton')
        more.setText('⋯')
        more.setToolTip('More')
        more.setPopupMode(QToolButton.InstantPopup)
        more.setMenu(self._more_menu())
        bar.addWidget(more)
        root.addLayout(bar)

        # sidebar | files | details
        split = QSplitter(Qt.Horizontal)
        self.sidebar = QListWidget()
        self.sidebar.setMinimumWidth(140)
        self.sidebar.setMaximumWidth(260)
        self.sidebar.itemClicked.connect(self._on_sidebar)
        split.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.list_view = QTreeView()
        self.list_view.setModel(self.proxy)
        self.list_view.setRootIsDecorated(False)
        self.list_view.setItemsExpandable(False)
        self.list_view.setUniformRowHeights(True)
        self.list_view.setSortingEnabled(True)
        self.list_view.sortByColumn(0, Qt.AscendingOrder)
        self.list_view.setAlternatingRowColors(False)
        self.list_view.header().setStretchLastSection(False)
        self.list_view.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 90), (2, 130), (3, 150)):
            self.list_view.setColumnWidth(col, width)
        self.icon_view = QListView()
        self.icon_view.setObjectName('fmIconView')     # a panel, not a popup list, in glass
        self.icon_view.setModel(self.proxy)
        self.icon_view.setViewMode(QListView.IconMode)
        self.icon_view.setIconSize(QSize(48, 48))
        self.icon_view.setGridSize(QSize(104, 92))
        self.icon_view.setResizeMode(QListView.Adjust)
        self.icon_view.setMovement(QListView.Static)
        self.icon_view.setWordWrap(True)
        self.icon_view.setUniformItemSizes(True)
        self.results_model = QStandardItemModel(0, 3, self)
        self.results_model.setHorizontalHeaderLabels(['Name', 'Folder', 'Date Modified'])
        self.results_view = QTreeView()
        self.results_view.setModel(self.results_model)
        self.results_view.setRootIsDecorated(False)
        self.results_view.setUniformRowHeights(True)
        self.results_view.setSortingEnabled(True)
        self.results_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_view.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.results_view.setColumnWidth(0, 240)
        for v in (self.list_view, self.icon_view, self.results_view):
            v.setSelectionMode(QAbstractItemView.ExtendedSelection)
            v.setContextMenuPolicy(Qt.CustomContextMenu)
            v.customContextMenuRequested.connect(self._context_menu)
            v.doubleClicked.connect(lambda _i: self.open_selected())
            v.setDragEnabled(v is not self.results_view)     # drag files out to other apps
            v.setDragDropMode(QAbstractItemView.DragOnly)
            self.stack.addWidget(v)
        for v in (self.list_view, self.icon_view):
            v.selectionModel().selectionChanged.connect(lambda *_: self._update_details())
        self.results_view.selectionModel().selectionChanged.connect(lambda *_: self._update_details())
        split.addWidget(self.stack)

        self.details = QFrame()
        self.details.setMinimumWidth(210)
        self.details.setMaximumWidth(280)
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(10, 4, 4, 4)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumHeight(140)
        self._d_name = QLabel()
        self._d_name.setWordWrap(True)
        f = QFont(self._d_name.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        self._d_name.setFont(f)
        self._d_info = QLabel()
        self._d_info.setWordWrap(True)
        self._d_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._d_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._btn_size = QPushButton('Calculate size')
        self._btn_size.clicked.connect(self._calculate_size)
        self._btn_open = QPushButton('Open')
        self._btn_open.clicked.connect(self.open_selected)
        self._btn_reveal = QPushButton(f'Show in {FINDER}')
        self._btn_reveal.clicked.connect(lambda: [_reveal(p) for p in self.selected_paths()[:1]])
        for w in (self._preview, self._d_name, self._d_info):
            dl.addWidget(w)
        dl.addStretch()
        for b in (self._btn_size, self._btn_open, self._btn_reveal):
            dl.addWidget(b)
        split.addWidget(self.details)
        for i, stretch in enumerate((0, 1, 0)):       # extra width goes to the files
            split.setStretchFactor(i, stretch)
            split.setCollapsible(i, i == 2)
        split.setSizes([180, 700, 240])
        root.addWidget(split, 1)

        self.status = QLabel()
        self.status.setStyleSheet('font-size: 11px;')
        root.addWidget(self.status)

        self._shortcuts()
        self._refresh_sidebar()
        self.model.directoryLoaded.connect(lambda _p: self._update_status())

    def _more_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction('New Folder', self.new_folder)
        m.addSeparator()
        self._act_hidden = QAction('Show Hidden Files', self, checkable=True)
        self._act_hidden.toggled.connect(self._set_hidden)
        m.addAction(self._act_hidden)
        self._act_details = QAction('Show Details Pane', self, checkable=True, checked=True)
        self._act_details.toggled.connect(lambda on: self.details.setVisible(on))
        m.addAction(self._act_details)
        m.addSeparator()
        m.addAction(f'Open This Folder in {FINDER}', lambda: open_path(self._path))
        m.addAction('Open This Folder in ST Terminal', lambda: self.open_in_terminal.emit(self._path))
        return m

    def _shortcuts(self) -> None:
        cmd = 'Ctrl'   # Qt maps Ctrl to ⌘ on macOS
        keys = [
            (f'{cmd}+C', self.copy), (f'{cmd}+X', self.cut), (f'{cmd}+V', self.paste),
            (f'{cmd}+D', self.duplicate), ('F2', self.rename),
            (f'{cmd}+Shift+N', self.new_folder), (f'{cmd}+O', self.open_selected),
            (f'{cmd}+F', lambda: (self.search_edit.setFocus(), self.search_edit.selectAll())),
            (f'{cmd}+L', lambda: (self.path_edit.setFocus(), self.path_edit.selectAll())),
            ('Alt+Left' if not IS_MAC else f'{cmd}+[', self.back),
            ('Alt+Right' if not IS_MAC else f'{cmd}+]', self.forward),
            ('Alt+Up' if not IS_MAC else f'{cmd}+Up', self.up),
            (f'{cmd}+Down', self.open_selected),
            ('Backspace' if IS_WINDOWS else f'{cmd}+Shift+.', self.up if IS_WINDOWS else
             lambda: self._act_hidden.toggle()),
            (f'{cmd}+Backspace' if IS_MAC else 'Delete', self.trash),
        ]
        for seq, slot in keys:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        for view in (self.list_view, self.icon_view, self.results_view):
            for key in ('Return', 'Enter'):
                sc = QShortcut(QKeySequence(key), view)
                sc.setContext(Qt.WidgetShortcut)
                sc.activated.connect(self.open_selected)
            if IS_MAC:
                sc = QShortcut(QKeySequence('Space'), view)
                sc.setContext(Qt.WidgetShortcut)
                sc.activated.connect(self.quick_look)

    # ── sidebar ───────────────────────────────────────────────────────────────

    def _refresh_sidebar(self) -> None:
        items = [('Favorites', None)] + favourites() + [('Locations', None)] + locations()
        current = [(self.sidebar.item(i).text(), self.sidebar.item(i).data(Qt.UserRole))
                   for i in range(self.sidebar.count())]
        if [(t.strip(), p) for t, p in current] == [(t, p) for t, p in items]:
            return
        self.sidebar.clear()
        for label, path in items:
            it = QListWidgetItem(label)
            if path is None:                         # section title
                it.setFlags(Qt.NoItemFlags)
                f = it.font()
                f.setBold(True)
                f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
                it.setFont(f)
            else:
                it.setData(Qt.UserRole, path)
                it.setIcon(self._icons.icon(QFileInfo(path)))
                it.setToolTip(path)
            self.sidebar.addItem(it)
        self._mark_sidebar()

    def _mark_sidebar(self) -> None:
        self.sidebar.blockSignals(True)
        self.sidebar.clearSelection()
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).data(Qt.UserRole) == self._path:
                self.sidebar.setCurrentRow(i)
        self.sidebar.blockSignals(False)

    def _on_sidebar(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.go(path)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_sidebar()
        self._volume_timer.start()      # USB sticks / network drives come and go

    def hideEvent(self, event) -> None:
        self._volume_timer.stop()
        super().hideEvent(event)

    # ── navigation ────────────────────────────────────────────────────────────

    def go(self, path: str, remember: bool = True) -> bool:
        path = os.path.abspath(os.path.expanduser(path.strip() or '~'))
        if not os.path.isdir(path):
            if os.path.exists(path):          # a file: open its folder and select it
                self.go(os.path.dirname(path), remember)
                self._select(path)
                return True
            QMessageBox.warning(self, 'File Manager', f'There is no folder at:\n{path}')
            self.path_edit.setText(self._path)
            return False
        if not os.access(path, os.R_OK | os.X_OK):
            QMessageBox.warning(self, 'File Manager',
                                f'ST-SoftwareTool may not open this folder:\n{path}' +
                                ('\n\nAllow it in System Settings › Privacy & Security › '
                                 'Files and Folders (or Full Disk Access).' if IS_MAC else ''))
            return False
        if remember and self._path and path != self._path:
            self._back.append(self._path)
            self._forward.clear()
        self._stop_search()
        self._path = path
        self.model.setRootPath(path)
        src = self.model.index(path)
        self.proxy.root_path = self.model.filePath(src)      # Qt's form ('/' on Windows too)
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self.proxy.set_filter('')
        root = self.proxy.mapFromSource(src)
        self.list_view.setRootIndex(root)
        self.icon_view.setRootIndex(root)
        if self.stack.currentWidget() is self.results_view:
            self._set_view(1 if self._btn_icons.isChecked() else 0)
        self.path_edit.setText(path)
        self._btn_back.setEnabled(bool(self._back))
        self._btn_fwd.setEnabled(bool(self._forward))
        self._btn_up.setEnabled(os.path.dirname(path) != path)
        self._mark_sidebar()
        self._update_details()
        self._update_status()
        return True

    def back(self) -> None:
        if self._back:
            self._forward.append(self._path)
            self.go(self._back.pop(), remember=False)

    def forward(self) -> None:
        if self._forward:
            self._back.append(self._path)
            self.go(self._forward.pop(), remember=False)

    def up(self) -> None:
        parent = os.path.dirname(self._path)
        if parent and parent != self._path:
            child = self._path
            self.go(parent)
            self._select(child)

    def _set_view(self, mode: int) -> None:
        self._btn_list.setChecked(mode == 0)
        self._btn_icons.setChecked(mode == 1)
        self.stack.setCurrentIndex(mode)

    def current_view(self) -> QAbstractItemView:
        return self.stack.currentWidget()

    # ── selection ─────────────────────────────────────────────────────────────

    def selected_paths(self) -> List[str]:
        view = self.current_view()
        rows = view.selectionModel().selectedRows(0) if view is not self.icon_view \
            else view.selectionModel().selectedIndexes()
        out = []
        for idx in rows:
            if view is self.results_view:
                item = self.results_model.item(idx.row(), 0)
                out.append(item.data(Qt.UserRole))
            else:
                out.append(self.model.filePath(self.proxy.mapToSource(idx)))
        return [p for p in dict.fromkeys(out) if p]

    def _select(self, path: str) -> None:
        def later():
            idx = self.proxy.mapFromSource(self.model.index(path))
            if idx.isValid():
                view = self.current_view()
                view.setCurrentIndex(idx)
                view.scrollTo(idx)
        QTimer.singleShot(150, later)        # the model lists folders in the background

    # ── opening ───────────────────────────────────────────────────────────────

    def open_selected(self) -> None:
        paths = self.selected_paths()
        if len(paths) == 1 and os.path.isdir(paths[0]) and not _is_bundle(paths[0]):
            self.go(paths[0])
            return
        for p in paths:
            if os.path.isdir(p) and not _is_bundle(p):
                continue
            try:
                open_path(p)
            except OSError as exc:
                QMessageBox.warning(self, 'Open', f'Could not open {os.path.basename(p)}:\n{exc}')

    def quick_look(self) -> None:
        paths = self.selected_paths()
        if paths and IS_MAC:
            subprocess.Popen(['/usr/bin/qlmanage', '-p', *paths[:20]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ── clipboard ─────────────────────────────────────────────────────────────

    def _to_clipboard(self, paths: List[str], cut: bool) -> None:
        if not paths:
            return
        self._clip, self._clip_cut = paths, cut
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        data.setText('\n'.join(paths))
        QGuiApplication.clipboard().setMimeData(data)
        verb = 'Cut' if cut else 'Copied'
        self.status.setText(f'{verb} {len(paths)} item{"s" if len(paths) != 1 else ""} — '
                            f'paste with {"⌘" if IS_MAC else "Ctrl+"}V')

    def copy(self) -> None:
        self._to_clipboard(self.selected_paths(), cut=False)

    def cut(self) -> None:
        self._to_clipboard(self.selected_paths(), cut=True)

    def copy_path(self) -> None:
        paths = self.selected_paths() or [self._path]
        QGuiApplication.clipboard().setText('\n'.join(paths))

    def _clipboard_paths(self) -> List[str]:
        """Files on the clipboard — ours, or copied in Finder / Explorer."""
        urls = QGuiApplication.clipboard().mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths and paths != self._clip:
            self._clip, self._clip_cut = paths, False
        return [p for p in self._clip if os.path.exists(p)]

    def paste(self) -> None:
        paths = self._clipboard_paths()
        if paths:
            self._transfer(paths, self._path, move=self._clip_cut)
            if self._clip_cut:
                self._clip, self._clip_cut = [], False

    def duplicate(self) -> None:
        paths = self.selected_paths()
        if paths:
            self._transfer(paths, None, move=False)

    def _transfer(self, paths: List[str], dest: Optional[str], move: bool) -> None:
        """Copy or move in the background. dest=None duplicates next to the original."""
        if self._busy:
            QMessageBox.information(self, 'File Manager', 'Another copy is still running.')
            return
        for p in paths:
            target = dest or os.path.dirname(p)
            if os.path.isdir(p) and (os.path.abspath(target) + os.sep).startswith(
                    os.path.abspath(p) + os.sep):
                QMessageBox.warning(self, 'File Manager',
                                    f'"{os.path.basename(p)}" can\'t be put inside itself.')
                return

        def work():
            done, errors, last = 0, [], None
            for p in paths:
                folder = dest or os.path.dirname(p)
                try:
                    if move and os.path.dirname(os.path.abspath(p)) == os.path.abspath(folder):
                        continue                      # cut and pasted into the same folder
                    target = _unique(folder, os.path.basename(p))
                    if move:
                        shutil.move(p, target)
                    elif os.path.isdir(p) and not os.path.islink(p):
                        shutil.copytree(p, target, symlinks=True)
                    else:
                        shutil.copy2(p, target, follow_symlinks=False)
                    done += 1
                    last = target
                except (OSError, shutil.Error) as exc:
                    errors.append(f'{os.path.basename(p)}: {exc}')
            return done, errors, last

        self._busy = True
        what = 'Moving' if move else 'Copying'
        self.status.setText(f'{what} {len(paths)} item{"s" if len(paths) != 1 else ""}…')

        def finished(result):
            self._busy = False
            if not isinstance(result, tuple) or len(result) != 3:
                QMessageBox.warning(self, 'File Manager', str(result))
                return
            done, errors, last = result
            self._update_status()
            if last:
                self._select(last)
            if errors:
                QMessageBox.warning(self, 'File Manager',
                                    'Some items could not be ' + ('moved' if move else 'copied')
                                    + ':\n\n' + '\n'.join(errors[:12]))
        call_async(self, work, finished)

    # ── changing things ───────────────────────────────────────────────────────

    def new_folder(self) -> None:
        if self.stack.currentWidget() is self.results_view:
            return
        target = _unique(self._path, 'untitled folder' if not IS_WINDOWS else 'New folder')
        try:
            os.mkdir(target)
        except OSError as exc:
            QMessageBox.warning(self, 'New Folder', f'Could not make a folder here:\n{exc}')
            return
        self._select(target)
        QTimer.singleShot(250, lambda: self.rename([target]))

    def rename(self, paths: Optional[List[str]] = None) -> None:
        paths = paths or self.selected_paths()
        if len(paths) != 1:
            return
        old = paths[0]
        name, ok = QInputDialog.getText(self, 'Rename', 'New name:', QLineEdit.Normal,
                                        os.path.basename(old))
        name = name.strip()
        if not ok or not name or name == os.path.basename(old):
            return
        if '/' in name or (IS_WINDOWS and any(c in name for c in '\\:*?"<>|')):
            QMessageBox.warning(self, 'Rename', 'That name has characters a file name can\'t have.')
            return
        new = os.path.join(os.path.dirname(old), name)
        if os.path.exists(new) and os.path.normcase(new) != os.path.normcase(old):
            QMessageBox.warning(self, 'Rename', f'"{name}" already exists here.')
            return
        try:
            os.rename(old, new)
        except OSError as exc:
            QMessageBox.warning(self, 'Rename', f'Could not rename it:\n{exc}')
            return
        self._select(new)

    def trash(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        what = (f'"{os.path.basename(paths[0])}"' if len(paths) == 1 else f'{len(paths)} items')
        if QMessageBox.question(self, f'Move to {TRASH}', f'Move {what} to the {TRASH}?',
                                QMessageBox.Yes | QMessageBox.Cancel,
                                QMessageBox.Yes) != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile

        def to_trash(p):
            r = QFile.moveToTrash(p)
            return r[0] if isinstance(r, tuple) else bool(r)
        failed = [p for p in paths if not to_trash(p)]
        if failed:
            QMessageBox.warning(self, f'Move to {TRASH}',
                                'These could not be moved (no permission?):\n\n' +
                                '\n'.join(os.path.basename(p) for p in failed[:12]))
        self._update_status()

    # ── context menu ──────────────────────────────────────────────────────────

    def _context_menu(self, pos) -> None:
        view = self.current_view()
        idx = view.indexAt(pos)
        if idx.isValid() and not view.selectionModel().isSelected(idx):
            view.setCurrentIndex(idx)
        paths = self.selected_paths() if idx.isValid() else []
        m = QMenu(self)
        cmd = '⌘' if IS_MAC else 'Ctrl+'
        if paths:
            m.addAction('Open', self.open_selected)
            if IS_MAC:
                m.addAction('Quick Look', self.quick_look)
            m.addAction(f'Show in {FINDER}', lambda: _reveal(paths[0]))
            m.addSeparator()
            m.addAction(f'Copy\t{cmd}C', self.copy)
            m.addAction(f'Cut\t{cmd}X', self.cut)
            m.addAction(f'Duplicate\t{cmd}D', self.duplicate)
            m.addAction('Copy Path', self.copy_path)
            if len(paths) == 1:
                m.addAction('Rename…\tF2', self.rename)
            if len(paths) == 1 and os.path.isdir(paths[0]):
                m.addAction('Open in ST Terminal', lambda: self.open_in_terminal.emit(paths[0]))
            m.addSeparator()
            m.addAction(f'Move to {TRASH}', self.trash)
        else:
            m.addAction(f'New Folder\t{cmd}⇧N' if IS_MAC else 'New Folder\tCtrl+Shift+N',
                        self.new_folder)
            paste = m.addAction(f'Paste\t{cmd}V', self.paste)
            paste.setEnabled(bool(self._clipboard_paths()))
            m.addSeparator()
            m.addAction('Copy Path of This Folder', self.copy_path)
            m.addAction('Open This Folder in ST Terminal', lambda: self.open_in_terminal.emit(self._path))
            m.addAction(f'Open This Folder in {FINDER}', lambda: open_path(self._path))
        m.exec(view.viewport().mapToGlobal(pos))

    # ── details & status ──────────────────────────────────────────────────────

    def _update_details(self) -> None:
        paths = self.selected_paths()
        path = paths[0] if len(paths) == 1 else None
        if len(paths) > 1:
            self._preview.setPixmap(self._icons.icon(QFileInfo(paths[0])).pixmap(96, 96))
            self._d_name.setText(f'{len(paths)} items')
            total = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
            self._d_info.setText(f'Files: {_fmt_size(total)}')
            self._btn_size.setVisible(False)
            self._btn_open.setEnabled(True)
            self._btn_reveal.setEnabled(True)
            return
        path = path or self._path
        info = QFileInfo(path)
        pix = None
        if info.isFile() and info.suffix().lower() in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp',
                                                       'heic', 'tif', 'tiff', 'ico', 'svg'):
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(QSize(200, 140), Qt.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                pix = QPixmap.fromImage(img)
        self._preview.setPixmap(pix or self._icons.icon(info).pixmap(96, 96))
        self._d_name.setText(info.fileName() or path)
        kind = _kind(info, self._icons)
        lines = [f'<b>Kind</b>  {kind}']
        if info.isFile():
            lines.append(f'<b>Size</b>  {_fmt_size(info.size())}')
        mod = info.lastModified().toString('d MMM yyyy, HH:mm')
        born = info.birthTime()
        if born.isValid():
            lines.append(f'<b>Created</b>  {born.toString("d MMM yyyy, HH:mm")}')
        lines.append(f'<b>Modified</b>  {mod}')
        lines.append(f'<b>Where</b>  {os.path.dirname(path) or path}')
        self._d_info.setText('<br>'.join(lines))
        self._btn_size.setVisible(info.isDir())
        self._btn_size.setEnabled(not self._busy)
        self._btn_size.setText('Calculate size')
        self._btn_open.setEnabled(bool(paths))
        self._btn_reveal.setEnabled(True)
        self._size_target = path

    def _calculate_size(self) -> None:
        target = getattr(self, '_size_target', None)
        if not target:
            return
        self._btn_size.setEnabled(False)
        self._btn_size.setText('Calculating…')

        def work():
            total = count = 0
            for folder, dirs, files in os.walk(target, onerror=lambda e: None):
                for f in files:
                    try:
                        total += os.lstat(os.path.join(folder, f)).st_size
                        count += 1
                    except OSError:
                        pass
            return total, count

        def done(result):
            if getattr(self, '_size_target', None) != target or not isinstance(result, tuple):
                return
            total, count = result
            self._btn_size.setText(f'{_fmt_size(total)}  ·  {count:,} files')

        call_async(self, work, done)

    def _update_status(self) -> None:
        if self.stack.currentWidget() is self.results_view:
            return
        root = self.proxy.mapFromSource(self.model.index(self._path))
        n = self.proxy.rowCount(root)
        free = QStorageInfo(self._path)
        avail = f'  ·  {_fmt_size(free.bytesAvailable())} available' if free.isValid() else ''
        self.status.setText(f'{n} item{"s" if n != 1 else ""}{avail}')

    # ── search ────────────────────────────────────────────────────────────────

    def _on_search_text(self, text: str) -> None:
        if self.stack.currentWidget() is self.results_view and not text:
            self._stop_search()
            self._set_view(1 if self._btn_icons.isChecked() else 0)
        self.proxy.set_filter(text)
        root = self.proxy.mapFromSource(self.model.index(self._path))
        self.list_view.setRootIndex(root)
        self.icon_view.setRootIndex(root)
        self._update_status()

    def _search_subfolders(self) -> None:
        text = self.search_edit.text().strip()
        if not text:
            return
        self._stop_search()
        self.results_model.removeRows(0, self.results_model.rowCount())
        self.stack.setCurrentWidget(self.results_view)
        self.status.setText(f'Searching “{text}” in {self._path}…')
        w = _SearchWorker(self._path, text, self._act_hidden.isChecked(), self)
        w.found.connect(self._add_results)
        w.done.connect(lambda total, stopped: self.status.setText(
            f'{total} found for “{text}”' + (' (showing the first ones)' if stopped and total >= _MAX_RESULTS
                                              else ' (stopped)' if stopped else '')))
        w.finished.connect(w.deleteLater)
        self._search = w
        w.start()

    def _add_results(self, rows: list) -> None:
        for name, folder, is_dir, mtime in rows:
            path = os.path.join(folder, name)
            item = QStandardItem(self._icons.icon(QFileInfo(path)), name)
            item.setData(path, Qt.UserRole)
            when = datetime.fromtimestamp(mtime).strftime('%d %b %Y, %H:%M') if mtime else ''
            self.results_model.appendRow([item, QStandardItem(folder), QStandardItem(when)])

    def _stop_search(self) -> None:
        if self._search is not None:
            try:
                if self._search.isRunning():
                    self._search.requestInterruption()
                    self._search.wait(2000)
            except RuntimeError:
                pass
            self._search = None

    def _set_hidden(self, show: bool) -> None:
        flags = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs
        if show:
            flags |= QDir.Hidden | QDir.System
        self.model.setFilter(flags)

    def cleanup(self) -> None:
        self._stop_search()
