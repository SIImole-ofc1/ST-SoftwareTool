import os
import sys
import threading
import winreg
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QLineEdit, QGroupBox, QSplitter, QTextEdit,
    QComboBox, QRadioButton, QButtonGroup, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout,
    QFileDialog, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QApplication,
    QScrollArea, QTableView, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QFont, QColor, QBrush
from core.device_manager import DeviceManager
from core.performance_monitor import PerformanceMonitor
from core.task_manager import TaskManagerBackend
from ui.graph_widget import UsageGraph
from ui.task_table import ProcessTableModel, HistoryTableModel, TaskSortProxy
from ui.services_view import ServicesView
from ui.startup_view import StartupView
from ui.device_settings_view import DeviceSettingsView
from ui.antivirus_view import AntivirusView
from ui.vpn_view import VpnView

# (column_index, Qt.SortOrder) pairs used by the sort combo
_TASK_SORT_PROXY = [
    ("Most CPU Usage",  2, Qt.DescendingOrder),
    ("Least CPU Usage", 2, Qt.AscendingOrder),
    ("Most RAM Usage",  3, Qt.DescendingOrder),
    ("Least RAM Usage", 3, Qt.AscendingOrder),
    ("First Started",   6, Qt.AscendingOrder),
    ("Last Started",    6, Qt.DescendingOrder),
    ("Name  A → Z",     0, Qt.AscendingOrder),
    ("By PID",          1, Qt.AscendingOrder),
]


# ── background GPU polling thread ─────────────────────────────────────────────

class _GpuWorker(QThread):
    """Polls GPU utilization in a background thread every 3 s."""
    gpu_ready     = Signal(float)   # -1.0 = not available
    details_ready = Signal(dict)    # GPU details + cpu_temp key, refreshed every 15 s

    def __init__(self, monitor: PerformanceMonitor, parent=None):
        super().__init__(parent)
        self._mon = monitor

    def run(self):
        tick = 0
        while not self.isInterruptionRequested():
            val = self._mon.gpu_percent()
            self.gpu_ready.emit(val if val is not None else -1.0)
            # Every 5 ticks (≈15 s) refresh full details and CPU temp
            if tick % 5 == 0:
                details = self._mon.gpu_details() if val is not None else {}
                details['cpu_temp'] = self._mon.cpu_temp()
                self.details_ready.emit(details)
            tick += 1
            self.msleep(3000)


# ── background task-list polling thread ──────────────────────────────────────

class _TaskWorker(QThread):
    data_ready = Signal(list, list)

    def __init__(self, backend: TaskManagerBackend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._wake    = threading.Event()
        self._active  = threading.Event()

    def activate(self):
        """Tab became visible — start subprocess and begin streaming data."""
        self._active.set()
        self._wake.set()

    def deactivate(self):
        """Tab hidden — kill subprocess so readline() unblocks immediately."""
        self._active.clear()
        self._backend.stop_monitor()
        self._wake.set()

    def wake_now(self):
        """Refresh button — next line arrives in ≤1.8 s from the subprocess."""
        self._wake.set()

    def run(self):
        while not self.isInterruptionRequested():
            if not self._active.is_set():
                # Parked: wait until activated or interrupted
                self._wake.wait(timeout=60.0)
                self._wake.clear()
                continue

            # Ensure proc_monitor subprocess is running
            if not self._backend.is_alive():
                if not self._backend.start_monitor():
                    self._wake.wait(timeout=5.0)
                    self._wake.clear()
                    continue

            # Block here ~1.8 s while proc_monitor collects data.
            # No GIL impact on the main thread — psutil runs in a separate process.
            procs = self._backend.read_procs()
            if procs is not None:
                self.data_ready.emit(procs, self._backend.history())


# ── background perf polling thread (keeps disk/net off the main thread) ───────

class _PerfWorker(QThread):
    perf_ready = Signal(object)   # emits a plain dict

    def __init__(self, monitor: PerformanceMonitor, parent=None):
        super().__init__(parent)
        self._mon = monitor

    def run(self):
        # Warm up rate-based counters on THIS thread so the first real
        # sample has a valid baseline interval
        self._mon.cpu_percent()
        self._mon.network()
        self._mon.disk_io()
        self.msleep(1000)
        while not self.isInterruptionRequested():
            self.perf_ready.emit({
                'cpu':  self._mon.cpu_percent(),
                'freq': self._mon.cpu_freq(),
                'ram':  self._mon.ram(),
                'disk': self._mon.disk_io(),
                'net':  self._mon.network(),
            })
            self.msleep(1000)


# ── background device-load thread ─────────────────────────────────────────────

class _DevWorker(QThread):
    done  = Signal(list)
    error = Signal(str)

    def __init__(self, backend: DeviceManager, parent=None):
        super().__init__(parent)
        self._backend = backend

    def run(self):
        try:
            self.done.emit(self._backend.get_devices())
        except Exception as exc:
            self.error.emit(str(exc))


# ── background app-scan thread ────────────────────────────────────────────────

class _ScanWorker(QThread):
    done  = Signal(list)
    error = Signal(str)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager

    def run(self):
        try:
            self.done.emit(self._manager.scan_all())
        except Exception as exc:
            self.error.emit(str(exc))


# ── Add-app dialog ────────────────────────────────────────────────────────────

class AddAppDialog(QDialog):
    def __init__(self, categories: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Application")
        self.setFixedSize(460, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.path_edit = QLineEdit()
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(sorted(categories))
        self.desc_edit = QLineEdit()

        browse = QPushButton("Browse…")
        browse.setFixedWidth(80)
        browse.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse)

        form.addRow("Name:", self.name_edit)
        form.addRow("Path:", path_row)
        form.addRow("Category:", self.cat_combo)
        form.addRow("Description:", self.desc_edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Application", "", "Executables (*.exe *.bat *.cmd);;All Files (*)"
        )
        if path:
            self.path_edit.setText(path)
            if not self.name_edit.text():
                self.name_edit.setText(os.path.splitext(os.path.basename(path))[0])

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "path": self.path_edit.text().strip(),
            "category": self.cat_combo.currentText(),
            "description": self.desc_edit.text().strip(),
        }


# ── Rename dialog ─────────────────────────────────────────────────────────────

class RenameDialog(QDialog):
    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename App")
        self.setFixedSize(320, 100)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(current_name)
        form.addRow("New name:", self.name_edit)
        layout.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def new_name(self) -> str:
        return self.name_edit.text().strip()


# ── Main GUI view ─────────────────────────────────────────────────────────────

class GUIView(QWidget):
    switch_to_terminal = Signal()
    settings_applied   = Signal(str, bool)  # (theme_key, suggestions_enabled)
    settings_changed   = Signal()            # protection / privacy settings changed

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager          = manager
        self._current_category = "All"
        self._dev_manager     = DeviceManager()
        self._all_devices     = []

        # Task manager
        self._task_backend = TaskManagerBackend()
        self._task_worker  = _TaskWorker(self._task_backend, self)
        self._task_worker.data_ready.connect(self._on_tasks_ready)

        # Performance monitoring
        self._perf      = PerformanceMonitor()
        self._cpu_info  = {}
        self._gpu_info  = {}
        self._gpu_ok    = None   # None=unknown, True/False after first poll

        # Graph widget refs (set by _sub_performance)
        self._g_cpu = self._g_gpu = self._g_ram = None
        self._g_disk = self._g_net_up = self._g_net_down = None

        # Background perf worker — ALL disk/net/cpu calls stay off the main thread
        self._perf_worker = _PerfWorker(self._perf, self)
        self._perf_worker.perf_ready.connect(self._on_perf_ready)

        self._cpu_temp: Optional[float] = None

        # GPU worker thread
        self._gpu_worker = _GpuWorker(self._perf, self)
        self._gpu_worker.gpu_ready.connect(self._on_gpu_ready)
        self._gpu_worker.details_ready.connect(self._on_gpu_details)

        # Lazy-loaded tab widgets (created once, reused)
        self._svc_view        = ServicesView(self)
        self._startup_view    = StartupView(self)
        self._dev_settings    = DeviceSettingsView(self)
        self._antivirus_view  = AntivirusView(self)
        self._vpn_view        = VpnView(self)

        self._build_ui()
        self.refresh()

        # Apply saved theme to sidebar immediately
        _saved_theme = self.manager.settings.get("theme", "dark")
        self._dev_settings.set_theme(_saved_theme)

        if self._perf.available:
            self._cpu_info = self._perf.cpu_info()
            self._perf_worker.start()
            self._gpu_worker.start()

        if self._task_backend.available:
            self._task_worker.start()

    def cleanup(self):
        # Stop subprocess first so the background thread unblocks from readline()
        self._task_backend.cleanup()
        for w in (self._perf_worker, self._gpu_worker, self._task_worker):
            if w.isRunning():
                w.requestInterruption()
                if hasattr(w, 'wake_now'):
                    w.wake_now()
                w.wait(5000)
        self._svc_view.cleanup()
        self._startup_view.cleanup()
        self._antivirus_view.cleanup()
        self._vpn_view.cleanup()

    # ── construction ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(self._tab_apps(),        "App Manager")
        self.tabs.addTab(self._tab_pinned(),      "Pinned  ★")
        self.tabs.addTab(self._tab_devices(),     "Device Manager")
        self._task_tab_idx    = self.tabs.addTab(self._tab_tasks(),    "Task Manager")
        self._svc_tab_idx     = self.tabs.addTab(self._svc_view,       "Services")
        self.tabs.addTab(self._tab_settings(),    "Settings")
        self._devsettings_tab_idx = self.tabs.addTab(self._dev_settings, "Device Settings")
        self._startup_tab_idx   = self.tabs.addTab(self._startup_view,   "Advanced Startup")
        self._antivirus_tab_idx = self.tabs.addTab(self._antivirus_view, "AntiVirus")
        self.tabs.addTab(self._vpn_view, "VPN")
        self.tabs.currentChanged.connect(self._on_main_tab_changed)

    # ── tab: App Manager ──────────────────────────────────────────────────────

    def _tab_apps(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search applications…")
        self.search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_input)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Horizontal)

        left = QGroupBox("Categories")
        ll = QVBoxLayout(left)
        self.cat_list = QListWidget()
        self.cat_list.currentTextChanged.connect(self._on_cat_changed)
        ll.addWidget(self.cat_list)
        splitter.addWidget(left)

        center = QGroupBox("Applications")
        cl = QVBoxLayout(center)
        self.app_list = QListWidget()
        self.app_list.currentItemChanged.connect(self._on_app_sel)
        self.app_list.itemDoubleClicked.connect(self._launch_sel)
        cl.addWidget(self.app_list)
        splitter.addWidget(center)

        right = QGroupBox("Details")
        rl = QVBoxLayout(right)
        self.detail_box = QTextEdit(readOnly=True)
        self.detail_box.setFont(QFont("Consolas", 9))
        rl.addWidget(self.detail_box)
        splitter.addWidget(right)

        splitter.setSizes([150, 320, 200])
        layout.addWidget(splitter, 1)

        bar = QHBoxLayout()
        self.btn_launch = QPushButton("Launch")
        self.btn_add    = QPushButton("Add App…")
        self.btn_remove = QPushButton("Remove")
        self.btn_pin    = QPushButton("Pin  ★")
        self.btn_rename = QPushButton("Rename…")
        self.btn_scan   = QPushButton("Scan System")
        self.btn_term   = QPushButton("Terminal Mode")

        self.btn_launch.clicked.connect(self._launch_sel)
        self.btn_add.clicked.connect(self._add_app)
        self.btn_remove.clicked.connect(self._remove_sel)
        self.btn_pin.clicked.connect(self._toggle_pin)
        self.btn_rename.clicked.connect(self._rename_sel)
        self.btn_scan.clicked.connect(self._scan)
        self.btn_term.clicked.connect(self.switch_to_terminal.emit)

        for b in (self.btn_launch, self.btn_add, self.btn_remove,
                  self.btn_pin, self.btn_rename, self.btn_scan):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.btn_term)
        layout.addLayout(bar)
        return tab

    # ── tab: Pinned ───────────────────────────────────────────────────────────

    def _tab_pinned(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Favourite / Pinned Applications"))

        splitter = QSplitter(Qt.Horizontal)

        left = QGroupBox("Pinned Apps")
        ll = QVBoxLayout(left)
        self.pinned_list = QListWidget()
        self.pinned_list.currentItemChanged.connect(self._on_pinned_sel)
        self.pinned_list.itemDoubleClicked.connect(self._launch_pinned)
        ll.addWidget(self.pinned_list)
        splitter.addWidget(left)

        right = QGroupBox("Details")
        rl = QVBoxLayout(right)
        self.pinned_detail = QTextEdit(readOnly=True)
        self.pinned_detail.setFont(QFont("Consolas", 9))
        rl.addWidget(self.pinned_detail)
        right.setFixedWidth(240)
        splitter.addWidget(right)

        layout.addWidget(splitter, 1)

        bar = QHBoxLayout()
        self.btn_launch_pin = QPushButton("Launch")
        self.btn_unpin      = QPushButton("Unpin")
        self.btn_launch_pin.clicked.connect(self._launch_pinned)
        self.btn_unpin.clicked.connect(self._unpin_sel)
        bar.addWidget(self.btn_launch_pin)
        bar.addWidget(self.btn_unpin)
        bar.addStretch()
        layout.addLayout(bar)
        return tab

    # ── tab: Device Manager (outer shell with sub-tabs) ───────────────────────

    def _tab_devices(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(4, 4, 4, 4)
        ol.setSpacing(0)

        self._device_inner = QTabWidget()
        self._device_inner.addTab(self._sub_devices(),     "Devices")
        self._device_inner.addTab(self._sub_performance(), "Performance")
        ol.addWidget(self._device_inner)
        return outer

    # ── sub-tab: Devices ──────────────────────────────────────────────────────

    def _sub_devices(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Filter row
        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self.dev_filter = QLineEdit()
        self.dev_filter.setPlaceholderText("Filter by name…")
        self.dev_filter.textChanged.connect(self._filter_devices)
        top.addWidget(self.dev_filter)
        self.dev_load_btn = QPushButton("Load Devices")
        self.dev_load_btn.clicked.connect(self._load_devices)
        top.addWidget(self.dev_load_btn)
        layout.addLayout(top)

        # Splitter: tree | details
        splitter = QSplitter(Qt.Horizontal)

        left = QGroupBox("Devices")
        ll = QVBoxLayout(left)
        self.dev_tree = QTreeWidget()
        self.dev_tree.setHeaderLabels(["Device", "Status"])
        self.dev_tree.setColumnWidth(0, 300)
        # Alternating rows disabled — Fusion default turns them bright blue
        # Disable built-in double-click expand so our handler doesn't double-toggle
        self.dev_tree.setExpandsOnDoubleClick(False)
        self.dev_tree.currentItemChanged.connect(self._on_dev_sel)
        self.dev_tree.itemDoubleClicked.connect(self._on_dev_double_click)
        ll.addWidget(self.dev_tree)
        splitter.addWidget(left)

        right = QGroupBox("Details")
        rl = QVBoxLayout(right)
        self.dev_detail = QTextEdit(readOnly=True)
        self.dev_detail.setFont(QFont("Consolas", 9))
        rl.addWidget(self.dev_detail)
        right.setFixedWidth(310)
        splitter.addWidget(right)

        layout.addWidget(splitter, 1)   # stretch=1 → fills all vertical space

        # Bottom bar — always anchored to the very bottom
        bar = QHBoxLayout()
        self.dev_disable_btn = QPushButton("Disable Device")
        self.dev_enable_btn  = QPushButton("Enable Device")
        self.dev_disable_btn.clicked.connect(self._dev_disable)
        self.dev_enable_btn.clicked.connect(self._dev_enable)
        self.dev_disable_btn.setEnabled(False)
        self.dev_enable_btn.setEnabled(False)
        notice = QLabel(
            "Protected devices (CPU, GPU, Monitor, System, Disk) cannot be disabled."
        )
        notice.setStyleSheet("font-size: 10px;")
        bar.addWidget(self.dev_disable_btn)
        bar.addWidget(self.dev_enable_btn)
        bar.addStretch()
        bar.addWidget(notice)
        layout.addLayout(bar)

        return tab

    # ── sub-tab: Performance ──────────────────────────────────────────────────

    def _sub_performance(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(8)

        if not self._perf.available:
            msg = QLabel("Performance monitor is unavailable.\n\nReinstall ST-SoftwareTool to fix this.")
            msg.setAlignment(Qt.AlignCenter)
            msg.setFont(QFont("Consolas", 10))
            layout.addWidget(msg)
            return tab

        # Row 1: CPU  +  GPU
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        _, self._g_cpu, _ = self._make_perf_card("CPU  Utilization", "#00ff41", row1)
        _, self._g_gpu, _ = self._make_perf_card("GPU  Utilization", "#4488ff", row1)

        # Row 2: RAM  +  Disk I/O
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        _, self._g_ram,  _ = self._make_perf_card("RAM  Usage",  "#ff8844", row2)
        _, self._g_disk, _ = self._make_perf_card("Disk  I/O",   "#ffcc00", row2)

        # Row 3: Network Up  +  Network Down
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        _, self._g_net_up,   _ = self._make_perf_card("Network  Upload",   "#00ccff", row3)
        _, self._g_net_down, _ = self._make_perf_card("Network  Download", "#ff44aa", row3)

        for row in (row1, row2, row3):
            layout.addLayout(row, 1)

        # Apply current theme to freshly created graphs
        current_theme = self.manager.settings.get("theme", "dark")
        self.set_perf_theme(current_theme)

        note = QLabel(
            "Disk scale: 400 MB/s = 100%.  "
            "Network scale: 10 MB/s = 100%.  "
            "GPU updates every ~3 s."
        )
        note.setStyleSheet("font-size: 9px;")
        layout.addWidget(note)
        return tab

    def _make_perf_card(self, title: str, color: str, parent_layout):
        """Adds a metric card (graph + More button) to parent_layout."""
        card = QWidget()
        card.setMinimumHeight(140)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 2)
        cl.setSpacing(2)

        graph = UsageGraph(title, color, card)
        cl.addWidget(graph, 1)

        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        more_btn = QPushButton("More")
        more_btn.setFixedSize(52, 18)
        more_btn.setStyleSheet("font-size:8px; padding:1px 4px;")
        more_btn.clicked.connect(graph.open_detail)
        row.addStretch()
        row.addWidget(more_btn)
        cl.addLayout(row)

        parent_layout.addWidget(card)
        return card, graph, None

    # ── performance update callbacks ──────────────────────────────────────────

    def _on_perf_ready(self, data: dict):
        """Slot — receives perf data from _PerfWorker (background thread → main thread via Qt signal)."""
        if not self._g_cpu:
            return
        cpu       = data['cpu']
        freq      = data['freq']
        ru, rt, rp = data['ram']
        rd, wd    = data['disk']
        ku, kd    = data['net']

        name = (self._cpu_info.get("name") or "CPU")[:28]
        pc   = self._cpu_info.get("physical", 0)
        lc   = self._cpu_info.get("logical", 0)
        temp_str = f"  |  {self._cpu_temp:.0f}°C" if self._cpu_temp else ""
        self._g_cpu.push(cpu, f"{name}  |  {freq[0]:.1f} GHz  |  {pc}P/{lc}L cores{temp_str}")

        self._g_ram.push(rp, f"{ru:.1f} / {rt:.1f} GB used")

        dpct = min(100.0, (rd + wd) / 4.0)
        self._g_disk.push(dpct, f"R: {self._fmt_mb(rd)}  |  W: {self._fmt_mb(wd)}")

        self._g_net_up.push(min(100.0, ku / 102.4), f"Up:  {self._fmt_kb(ku)}")
        self._g_net_down.push(min(100.0, kd / 102.4), f"Down: {self._fmt_kb(kd)}")

        if self._gpu_ok is False and self._g_gpu.subtitle == "":
            self._g_gpu.subtitle = "GPU data not available on this system"
            self._g_gpu.update()

    def _on_gpu_details(self, d: dict):
        self._gpu_info = d
        self._cpu_temp = d.get('cpu_temp')

    def _on_gpu_ready(self, val: float):
        if not self._g_gpu:
            return
        if val < 0:
            self._gpu_ok = False
            if self._g_gpu.subtitle == "":
                self._g_gpu.subtitle = "GPU data not available"
                self._g_gpu.update()
        else:
            self._gpu_ok = True
            name  = (self._gpu_info.get("name") or "GPU")[:32]
            vram  = self._gpu_info.get("vram_total_gb", 0)
            temp  = self._gpu_info.get("temp_c", 0)
            parts = [name]
            if vram:
                parts.append(f"{vram:.0f} GB VRAM")
            if temp:
                parts.append(f"{temp}°C")
            self._g_gpu.push(val, "  |  ".join(parts))

    def set_perf_theme(self, theme: str):
        """Propagate a theme change to all live graph widgets and themed sub-views."""
        for g in (self._g_cpu, self._g_gpu, self._g_ram,
                  self._g_disk, self._g_net_up, self._g_net_down):
            if g:
                g.set_theme(theme)
        self._dev_settings.set_theme(theme)

    @staticmethod
    def _fmt_mb(mb: float) -> str:
        if mb < 1.0:
            return f"{mb * 1024:.0f} KB/s"
        return f"{mb:.1f} MB/s"

    @staticmethod
    def _fmt_kb(kb: float) -> str:
        if kb >= 1024:
            return f"{kb / 1024:.1f} MB/s"
        return f"{kb:.0f} KB/s"

    # ── device manager helpers ────────────────────────────────────────────────

    def _load_devices(self):
        if getattr(self, '_dev_worker_active', False):
            return
        self.dev_load_btn.setEnabled(False)
        self.dev_load_btn.setText("Loading…")
        self.dev_filter.setEnabled(False)
        self._dev_worker_active = True
        w = _DevWorker(self._dev_manager, self)
        self._dev_worker_ref = w
        w.done.connect(self._on_devices_loaded)
        w.error.connect(self._on_devices_error)
        w.start()

    def _on_devices_loaded(self, devices: list):
        self._dev_worker_active = False
        self._all_devices = devices
        self._populate_dev_tree(devices)
        self.dev_load_btn.setEnabled(True)
        self.dev_load_btn.setText("Refresh")
        self.dev_filter.setEnabled(True)

    def _on_devices_error(self, msg: str):
        self._dev_worker_active = False
        QMessageBox.critical(self, "Device Manager", msg)
        self.dev_load_btn.setEnabled(True)
        self.dev_load_btn.setText("Refresh")
        self.dev_filter.setEnabled(True)

    def _populate_dev_tree(self, devices):
        self.dev_tree.clear()
        self.dev_detail.clear()
        self.dev_disable_btn.setEnabled(False)
        self.dev_enable_btn.setEnabled(False)

        groups = self._dev_manager.grouped(devices)
        for class_label, devs in groups.items():
            parent = QTreeWidgetItem(self.dev_tree)
            parent.setText(0, f"{class_label}  ({len(devs)})")
            parent.setData(0, Qt.UserRole, None)
            f = parent.font(0)
            f.setBold(True)
            parent.setFont(0, f)

            for dev in devs:
                child = QTreeWidgetItem(parent)
                child.setText(0, dev.name)
                child.setText(1, dev.status_display)
                child.setData(0, Qt.UserRole, dev)
                if dev.protected:
                    child.setForeground(0, QBrush(QColor("#888888")))
                    child.setForeground(1, QBrush(QColor("#888888")))
                elif dev.is_working:
                    child.setForeground(1, QBrush(QColor("#00cc33")))
                elif dev.status == "Error":
                    child.setForeground(1, QBrush(QColor("#ff4444")))
                else:
                    child.setForeground(1, QBrush(QColor("#ffcc00")))

            parent.setExpanded(False)

    def _filter_devices(self, text: str):
        if not self._all_devices:
            return
        q = text.strip().lower()
        filtered = (
            [d for d in self._all_devices if q in d.name.lower()] if q
            else self._all_devices
        )
        self._populate_dev_tree(filtered)

    def _on_dev_double_click(self, item, _col):
        if item.data(0, Qt.UserRole) is None:
            item.setExpanded(not item.isExpanded())

    def _on_dev_sel(self, current, _prev):
        if not current:
            return
        dev = current.data(0, Qt.UserRole)
        if dev is None:
            self.dev_detail.clear()
            self.dev_disable_btn.setEnabled(False)
            self.dev_enable_btn.setEnabled(False)
            return
        self.dev_detail.setPlainText(dev.detail_text())
        can_act = not dev.protected and dev.present
        self.dev_disable_btn.setEnabled(can_act and dev.is_working)
        self.dev_enable_btn.setEnabled(can_act and not dev.is_working)

    def _dev_disable(self):
        item = self.dev_tree.currentItem()
        if not item:
            return
        dev = item.data(0, Qt.UserRole)
        if not dev or dev.protected:
            return
        reply = QMessageBox.warning(
            self, "Disable Device",
            f"Disable '{dev.name}'?\n\nThis may cause the device to stop working immediately.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ok, msg = self._dev_manager.disable_device(dev.instance_id)
        if ok:
            dev.status = "Unknown"
            item.setText(1, dev.status_display)
            item.setForeground(1, QBrush(QColor("#ffcc00")))
            self.dev_detail.setPlainText(dev.detail_text())
            self.dev_disable_btn.setEnabled(False)
            self.dev_enable_btn.setEnabled(True)
        else:
            QMessageBox.critical(
                self, "Error",
                f"Could not disable device:\n{msg}\n\nTry running as Administrator."
            )

    def _dev_enable(self):
        item = self.dev_tree.currentItem()
        if not item:
            return
        dev = item.data(0, Qt.UserRole)
        if not dev or dev.protected:
            return
        ok, msg = self._dev_manager.enable_device(dev.instance_id)
        if ok:
            dev.status = "OK"
            item.setText(1, dev.status_display)
            item.setForeground(1, QBrush(QColor("#00cc33")))
            self.dev_detail.setPlainText(dev.detail_text())
            self.dev_disable_btn.setEnabled(True)
            self.dev_enable_btn.setEnabled(False)
        else:
            QMessageBox.critical(
                self, "Error",
                f"Could not enable device:\n{msg}\n\nTry running as Administrator."
            )

    # ── tab: Task Manager ─────────────────────────────────────────────────────

    def _tab_tasks(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(4, 4, 4, 4)
        ol.setSpacing(0)
        self._task_inner = QTabWidget()
        self._task_inner.addTab(self._sub_task_procs(), "Processes")
        self._task_inner.addTab(self._sub_task_hist(),  "History")
        ol.addWidget(self._task_inner)
        return outer

    def _sub_task_procs(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        if not self._task_backend.available:
            msg = QLabel("Task Manager is unavailable.\n\npsutil could not be loaded.\nTry reinstalling ST-SoftwareTool.")
            msg.setAlignment(Qt.AlignCenter)
            msg.setFont(QFont("Consolas", 10))
            layout.addWidget(msg)
            return tab

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self._task_filter = QLineEdit()
        self._task_filter.setPlaceholderText("Filter by name…")
        self._task_filter.textChanged.connect(self._apply_task_filter)
        top.addWidget(self._task_filter)
        top.addWidget(QLabel("Sort:"))
        self._task_sort_combo = QComboBox()
        for label, _col, _order in _TASK_SORT_PROXY:
            self._task_sort_combo.addItem(label)
        self._task_sort_combo.currentIndexChanged.connect(self._apply_task_sort)
        top.addWidget(self._task_sort_combo)
        self._task_refresh_btn = QPushButton("Refresh")
        self._task_refresh_btn.setFixedWidth(72)
        self._task_refresh_btn.clicked.connect(self._force_refresh_tasks)
        top.addWidget(self._task_refresh_btn)
        layout.addLayout(top)

        # MVC: source model → sort/filter proxy → view
        self._proc_model = ProcessTableModel(self)
        self._proc_proxy = TaskSortProxy(self)
        self._proc_proxy.setDynamicSortFilter(False)   # re-sort only on header click, not on every dataChanged
        self._proc_proxy.setSourceModel(self._proc_model)

        self._proc_view = QTableView()
        self._proc_view.setModel(self._proc_proxy)
        self._proc_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._proc_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._proc_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._proc_view.setAlternatingRowColors(True)
        self._proc_view.setSortingEnabled(True)
        self._proc_view.setShowGrid(False)
        self._proc_view.verticalHeader().setVisible(False)
        self._proc_view.verticalHeader().setDefaultSectionSize(22)
        hdr = self._proc_view.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col in range(6):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        self._proc_view.setColumnWidth(0, 220)
        self._proc_view.setColumnWidth(1,  60)
        self._proc_view.setColumnWidth(2,  65)
        self._proc_view.setColumnWidth(3,  65)
        self._proc_view.setColumnWidth(4,  80)
        self._proc_view.setColumnWidth(5,  80)
        self._proc_view.selectionModel().selectionChanged.connect(self._on_proc_selection)
        layout.addWidget(self._proc_view, 1)

        self._task_kill_btn  = QPushButton("End Task")
        self._task_kill_btn.setEnabled(False)
        self._task_kill_btn.clicked.connect(self._kill_task)
        self._task_count_lbl = QLabel("Processes: 0")
        bar = QHBoxLayout()
        bar.addWidget(self._task_kill_btn)
        bar.addStretch()
        bar.addWidget(self._task_count_lbl)
        layout.addLayout(bar)

        self._apply_task_sort(0)
        return tab

    def _sub_task_hist(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        if not self._task_backend.available:
            layout.addWidget(QLabel("psutil not available."))
            return tab

        self._hist_model = HistoryTableModel(self)

        self._hist_view = QTableView()
        self._hist_view.setModel(self._hist_model)
        self._hist_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._hist_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._hist_view.setAlternatingRowColors(True)
        self._hist_view.setShowGrid(False)
        self._hist_view.verticalHeader().setVisible(False)
        self._hist_view.verticalHeader().setDefaultSectionSize(22)
        hdr = self._hist_view.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col in range(3):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        self._hist_view.setColumnWidth(0, 240)
        self._hist_view.setColumnWidth(1,  60)
        self._hist_view.setColumnWidth(2,  80)
        layout.addWidget(self._hist_view, 1)

        bar = QHBoxLayout()
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(self._clear_task_history)
        self._hist_count_lbl = QLabel("Entries: 0")
        bar.addWidget(clear_btn)
        bar.addStretch()
        bar.addWidget(self._hist_count_lbl)
        layout.addLayout(bar)
        return tab

    # ── task manager callbacks ─────────────────────────────────────────────────

    def _on_tasks_ready(self, procs: list, hist: list):
        if hasattr(self, '_proc_model') and hasattr(self, '_task_count_lbl'):
            self._proc_model.set_procs(procs)
            self._task_count_lbl.setText(f"Processes: {self._proc_proxy.rowCount()}")
        if hasattr(self, '_hist_model') and hasattr(self, '_hist_count_lbl'):
            self._hist_model.set_entries(hist)
            self._hist_count_lbl.setText(f"Entries: {len(hist)}")

    def _on_main_tab_changed(self, idx: int):
        if idx == self._task_tab_idx:
            self._task_worker.activate()
        else:
            self._task_worker.deactivate()
        if idx == self._svc_tab_idx:
            self._svc_view.load_once()
        if idx == self._startup_tab_idx:
            self._startup_view.scan_once()

    def _on_proc_selection(self, selected, _deselected):
        self._task_kill_btn.setEnabled(bool(selected.indexes()))

    def _apply_task_filter(self):
        if hasattr(self, '_proc_proxy'):
            self._proc_proxy.setFilterFixedString(self._task_filter.text().strip())
            self._task_count_lbl.setText(f"Processes: {self._proc_proxy.rowCount()}")

    def _apply_task_sort(self, idx: int):
        if hasattr(self, '_proc_proxy'):
            _label, col, order = _TASK_SORT_PROXY[idx]
            self._proc_proxy.sort(col, order)

    def _force_refresh_tasks(self):
        self._task_worker.wake_now()

    def _kill_task(self):
        proxy_idx = self._proc_view.currentIndex()
        if not proxy_idx.isValid():
            return
        source_idx = self._proc_proxy.mapToSource(proxy_idx)
        p = self._proc_model.proc_at(source_idx.row())
        if p is None:
            return
        if QMessageBox.warning(
            self, "End Task",
            f"End task '{p.name}'  (PID {p.pid})?\n\nUnsaved work in that process will be lost.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        ok, err = self._task_backend.kill_process(p.pid)
        if not ok:
            if err == "already_gone":
                QMessageBox.information(self, "End Task", "Process has already ended.")
            elif err == "access_denied":
                QMessageBox.critical(self, "End Task",
                    "Access denied.\nTry running ST-SoftwareTool as Administrator.")
            else:
                QMessageBox.critical(self, "End Task", err)

    def _clear_task_history(self):
        self._task_backend.clear_history()
        if hasattr(self, '_hist_model'):
            self._hist_model.clear_all()
            self._hist_count_lbl.setText("Entries: 0")

    # ── tab: Settings ─────────────────────────────────────────────────────────

    def _tab_settings(self) -> QWidget:
        # Scroll area so groups don't get squashed on small windows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # ── Theme ─────────────────────────────────────────────────────────────
        theme_box = QGroupBox("Theme")
        tl = QVBoxLayout(theme_box)
        self.rb_dark    = QRadioButton("Dark  —  Black & Green")
        self.rb_dark_bw = QRadioButton("Dark  —  Black & White")
        self.rb_light   = QRadioButton("Light  —  Black & White")
        self.rb_hc      = QRadioButton("High Contrast")
        self.rb_win95   = QRadioButton("Classic")
        bg_theme = QButtonGroup(self)
        for rb in (self.rb_dark, self.rb_dark_bw, self.rb_light, self.rb_hc, self.rb_win95):
            bg_theme.addButton(rb)
            tl.addWidget(rb)
        current = self.manager.settings.get("theme", "dark")
        {
            "dark": self.rb_dark, "dark_bw": self.rb_dark_bw,
            "light": self.rb_light, "hc": self.rb_hc, "win95": self.rb_win95,
        }.get(current, self.rb_dark).setChecked(True)
        layout.addWidget(theme_box)

        # ── Default View ──────────────────────────────────────────────────────
        view_box = QGroupBox("Default View on Startup")
        vl = QVBoxLayout(view_box)
        self.rb_term = QRadioButton("Terminal")
        self.rb_gui  = QRadioButton("GUI")
        bg_view = QButtonGroup(self)
        bg_view.addButton(self.rb_term)
        bg_view.addButton(self.rb_gui)
        (self.rb_term if self.manager.settings.get("default_view", "terminal") == "terminal"
         else self.rb_gui).setChecked(True)
        vl.addWidget(self.rb_term)
        vl.addWidget(self.rb_gui)
        layout.addWidget(view_box)

        # ── Terminal ──────────────────────────────────────────────────────────
        terminal_box = QGroupBox("Terminal")
        tbox_l = QVBoxLayout(terminal_box)
        self.chk_suggestions = QCheckBox("Show command suggestions while typing")
        self.chk_suggestions.setChecked(
            self.manager.settings.get("terminal_suggestions", True)
        )
        tbox_l.addWidget(self.chk_suggestions)
        layout.addWidget(terminal_box)

        # ── Protection & Privacy ──────────────────────────────────────────────
        prot_box = QGroupBox("Protection & Privacy")
        pl = QVBoxLayout(prot_box)

        self.chk_run_background = QCheckBox(
            "Keep ST-SoftwareTool running in the background when window is closed"
        )
        self.chk_run_background.setChecked(
            self.manager.settings.get('run_in_background', False)
        )

        self.chk_start_windows = QCheckBox(
            "Start ST-SoftwareTool automatically with Windows"
        )
        self.chk_start_windows.setChecked(
            self.manager.settings.get('start_with_windows', False)
        )

        self.chk_auto_block = QCheckBox(
            "Automatically block critical threats detected during scans"
        )
        self.chk_auto_block.setChecked(
            self.manager.settings.get('auto_block_threats', True)
        )

        self.chk_privacy_monitor = QCheckBox(
            "Show popup notification when camera or microphone is turned on"
        )
        self.chk_privacy_monitor.setChecked(
            self.manager.settings.get('monitor_privacy', True)
        )

        self.chk_hourly_scan = QCheckBox(
            "Run automatic virus scan every hour and notify when complete"
        )
        self.chk_hourly_scan.setChecked(
            self.manager.settings.get('hourly_scan', True)
        )

        self.chk_auto_vpn = QCheckBox(
            "Auto-connect VPN (Tor) when ST-SoftwareTool starts"
        )
        self.chk_auto_vpn.setChecked(
            self.manager.settings.get('auto_vpn_startup', False)
        )

        for chk in (self.chk_run_background, self.chk_start_windows,
                    self.chk_auto_block, self.chk_privacy_monitor,
                    self.chk_hourly_scan, self.chk_auto_vpn):
            pl.addWidget(chk)

        layout.addWidget(prot_box)

        # ── Manage Categories ─────────────────────────────────────────────────
        cat_box = QGroupBox("Manage Categories")
        kal = QVBoxLayout(cat_box)
        self.settings_cat_list = QListWidget()
        kal.addWidget(self.settings_cat_list)
        cat_row = QHBoxLayout()
        self.cat_name_edit = QLineEdit()
        self.cat_name_edit.setPlaceholderText("Category name…")
        btn_add_cat = QPushButton("Add")
        btn_rm_cat  = QPushButton("Remove")
        btn_add_cat.clicked.connect(self._add_category)
        btn_rm_cat.clicked.connect(self._remove_category)
        cat_row.addWidget(self.cat_name_edit)
        cat_row.addWidget(btn_add_cat)
        cat_row.addWidget(btn_rm_cat)
        kal.addLayout(cat_row)
        layout.addWidget(cat_box)

        layout.addStretch()

        btn_apply = QPushButton("Apply Changes")
        btn_apply.setMinimumHeight(32)
        btn_apply.clicked.connect(self._apply_settings_btn)
        layout.addWidget(btn_apply)

        scroll.setWidget(tab)
        return scroll

    # ── refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        self._refresh_cats()
        self._refresh_apps()
        self._refresh_pinned()
        self._refresh_settings_cats()

    def _refresh_cats(self):
        self.cat_list.blockSignals(True)
        self.cat_list.clear()
        self.cat_list.addItem("All")
        for c in sorted(self.manager.categories):
            n = len(self.manager.get_apps(category=c))
            item = QListWidgetItem(f"{c}  ({n})")
            item.setData(Qt.UserRole, c)
            self.cat_list.addItem(item)
        self.cat_list.blockSignals(False)
        self.cat_list.setCurrentRow(0)

    def _refresh_apps(self, apps=None):
        self.app_list.clear()
        if apps is None:
            cat  = None if self._current_category == "All" else self._current_category
            apps = self.manager.get_apps(category=cat)
        for a in apps:
            label = f"{'★  ' if a.pinned else '    '}{a.name}"
            item  = QListWidgetItem(label)
            item.setData(Qt.UserRole, a)
            self.app_list.addItem(item)

    def _refresh_pinned(self):
        self.pinned_list.clear()
        for a in self.manager.get_pinned():
            item = QListWidgetItem(a.name)
            item.setData(Qt.UserRole, a)
            self.pinned_list.addItem(item)

    def _refresh_settings_cats(self):
        self.settings_cat_list.clear()
        for c in sorted(self.manager.categories):
            self.settings_cat_list.addItem(c)

    # ── event slots ───────────────────────────────────────────────────────────

    def _on_cat_changed(self, text: str):
        self._current_category = text.split("  (")[0] if "  (" in text else text
        self._refresh_apps()

    def _on_search(self, q: str):
        if q.strip():
            self._refresh_apps(self.manager.search_apps(q))
        else:
            self._refresh_apps()

    def _on_app_sel(self, cur, _prev):
        if cur:
            a = cur.data(Qt.UserRole)
            if a:
                self.detail_box.setPlainText(
                    f"Name:\n  {a.name}\n\n"
                    f"Path:\n  {a.path}\n\n"
                    f"Category:  {a.category}\n"
                    f"Pinned:    {'Yes ★' if a.pinned else 'No'}\n\n"
                    f"Description:\n  {a.description or '(none)'}"
                )

    def _on_pinned_sel(self, cur, _prev):
        if cur:
            a = cur.data(Qt.UserRole)
            if a:
                self.pinned_detail.setPlainText(
                    f"Name:\n  {a.name}\n\nPath:\n  {a.path}\n\n"
                    f"Category:  {a.category}\n\n"
                    f"Description:\n  {a.description or '(none)'}"
                )

    # ── actions ───────────────────────────────────────────────────────────────

    def _sel_app(self):
        item = self.app_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _sel_pinned(self):
        item = self.pinned_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _launch_sel(self):
        a = self._sel_app()
        if not a:
            return
        ok, msg = self.manager.launch_app(a.name)
        if not ok:
            QMessageBox.warning(self, "Launch Failed", msg)

    def _launch_pinned(self):
        a = self._sel_pinned()
        if not a:
            return
        ok, msg = self.manager.launch_app(a.name)
        if not ok:
            QMessageBox.warning(self, "Launch Failed", msg)

    def _add_app(self):
        dlg = AddAppDialog(self.manager.categories, self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["name"] or not v["path"]:
            QMessageBox.warning(self, "Error", "Name and path cannot be empty.")
            return
        ok, msg = self.manager.add_app(
            v["name"], v["path"], v["category"], v["description"]
        )
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "Error", msg)

    def _remove_sel(self):
        a = self._sel_app()
        if not a:
            return
        if (QMessageBox.question(self, "Remove",
                f"Remove '{a.name}' from AppManager?",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes):
            self.manager.remove_app(a.name)
            self.refresh()

    def _toggle_pin(self):
        a = self._sel_app()
        if not a:
            return
        self.manager.pin_app(a.name, not a.pinned)
        self.refresh()

    def _rename_sel(self):
        a = self._sel_app()
        if not a:
            return
        dlg = RenameDialog(a.name, self)
        if dlg.exec() != QDialog.Accepted:
            return
        new = dlg.new_name()
        if not new:
            return
        ok, msg = self.manager.rename_app(a.name, new)
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "Error", msg)

    def _unpin_sel(self):
        a = self._sel_pinned()
        if not a:
            return
        self.manager.pin_app(a.name, False)
        self.refresh()

    def _scan(self):
        if getattr(self, '_scan_worker_active', False):
            return
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("Scanning…")
        self._scan_worker_active = True
        w = _ScanWorker(self.manager, self)
        self._scan_worker_ref = w
        w.done.connect(self._on_scan_done)
        w.error.connect(self._on_scan_error)
        w.start()

    def _on_scan_done(self, found: list):
        self._scan_worker_active = False
        added = sum(1 for name, path, cat in found
                    if self.manager.add_app(name, path, cat)[0])
        QMessageBox.information(
            self, "Scan Complete",
            f"Found {len(found)} programs (registry + Start Menu + all desktops).\n"
            f"{added} newly imported with automatic categories."
        )
        self.refresh()
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("Scan System")

    def _on_scan_error(self, msg: str):
        self._scan_worker_active = False
        QMessageBox.critical(self, "Scan Error", msg)
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("Scan System")

    # ── settings tab actions ──────────────────────────────────────────────────

    def _apply_settings_btn(self):
        if self.rb_dark.isChecked():
            t = "dark"
        elif self.rb_dark_bw.isChecked():
            t = "dark_bw"
        elif self.rb_light.isChecked():
            t = "light"
        elif self.rb_hc.isChecked():
            t = "hc"
        else:
            t = "win95"
        v    = "terminal" if self.rb_term.isChecked() else "gui"
        sugg = self.chk_suggestions.isChecked()
        self.manager.settings["default_view"]        = v
        self.manager.settings["terminal_suggestions"] = sugg

        # Protection & Privacy settings
        self.manager.settings['run_in_background']  = self.chk_run_background.isChecked()
        self.manager.settings['auto_block_threats'] = self.chk_auto_block.isChecked()
        self.manager.settings['monitor_privacy']    = self.chk_privacy_monitor.isChecked()
        self.manager.settings['hourly_scan']        = self.chk_hourly_scan.isChecked()
        self.manager.settings['auto_vpn_startup']   = self.chk_auto_vpn.isChecked()
        want_startup = self.chk_start_windows.isChecked()
        self.manager.settings['start_with_windows'] = want_startup
        self._set_startup_registry(want_startup)

        self.settings_applied.emit(t, sugg)
        self.settings_changed.emit()
        try:
            self.manager.save()
        except Exception:
            pass

    @staticmethod
    def _set_startup_registry(enable: bool) -> None:
        _RUN_KEY  = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
        _APP_NAME = 'ST-SoftwareTool'
        # In compiled mode (PyInstaller or Nuitka) use the exe itself; no script path
        _pm = os.path.join(os.path.dirname(sys.executable), 'proc_monitor.exe')
        if getattr(sys, 'frozen', False) or os.path.exists(_pm):
            _EXE_PATH = f'"{sys.executable}"'
        else:
            try:
                import __main__
                script = getattr(__main__, '__file__', None)
                if script:
                    _EXE_PATH = f'"{sys.executable}" "{os.path.abspath(script)}"'
                else:
                    _EXE_PATH = f'"{sys.executable}"'
            except Exception:
                _EXE_PATH = f'"{sys.executable}"'
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
            )
            if enable:
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _EXE_PATH)
            else:
                try:
                    winreg.DeleteValue(key, _APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except OSError:
            pass

    def _add_category(self):
        name = self.cat_name_edit.text().strip()
        if not name:
            return
        ok, msg = self.manager.add_category(name)
        if ok:
            self.cat_name_edit.clear()
            self.refresh()
        else:
            QMessageBox.warning(self, "Error", msg)

    def _remove_category(self):
        item = self.settings_cat_list.currentItem()
        if not item:
            return
        ok, msg = self.manager.remove_category(item.text())
        if ok:
            self.refresh()
        else:
            QMessageBox.warning(self, "Error", msg)
