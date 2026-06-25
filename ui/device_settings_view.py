"""Device Settings UI — display, mouse, audio, NVIDIA, power, privacy."""
from __future__ import annotations
from typing import Optional, Dict

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QLabel, QSlider, QComboBox, QCheckBox, QPushButton,
    QGroupBox, QScrollArea, QSpinBox, QMessageBox, QSizePolicy, QFrame,
    QProgressBar,
)

import core.device_settings as DS


# ── helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> QGroupBox:
    g = QGroupBox(title.replace('&', '&&'))  # && prevents Qt mnemonic underscore
    g.setStyleSheet('QGroupBox { font-weight: bold; margin-top: 6px; }')
    return g

def _scroll(inner: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidget(inner)
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.NoFrame)
    return s

def _row(*widgets) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if isinstance(w, int):
            h.addSpacing(w)
        elif w is None:
            h.addStretch()
        else:
            h.addWidget(w)
    return h

def _lbl(text: str, color: str = '') -> QLabel:
    l = QLabel(text)
    if color:
        l.setStyleSheet(f'color: {color};')
    return l

def _btn(text: str, slot=None, width: int = 0) -> QPushButton:
    b = QPushButton(text)
    if width:
        b.setFixedWidth(width)
    if slot:
        b.clicked.connect(slot)
    return b

def _status(parent: QWidget) -> QLabel:
    l = QLabel('')
    l.setStyleSheet('color: #44aa44; font-size: 10px;')
    return l


# ── background worker for slow ops ───────────────────────────────────────────

class _Worker(QThread):
    done = Signal(object)
    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self._fn = fn; self._args = args
    def run(self):
        try: self.done.emit(self._fn(*self._args))
        except Exception as e: self.done.emit(e)


# ── pages ─────────────────────────────────────────────────────────────────────

class _DisplayPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolutions = []
        self._build()
        self._load()

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        # Resolution group
        g = _section('Resolution & Refresh Rate')
        gl = QVBoxLayout(g)

        info = DS.get_display_info()
        cur = f"{info.get('width','?')}×{info.get('height','?')}  @  {info.get('hz','?')} Hz"
        self._cur_lbl = QLabel(f'Current: {cur}')
        self._cur_lbl.setStyleSheet('font-size: 11px;')
        gl.addWidget(self._cur_lbl)

        self._res_combo = QComboBox()
        self._res_combo.setMinimumWidth(280)
        self._hz_combo  = QComboBox()
        self._hz_combo.setMinimumWidth(100)
        self._res_combo.currentIndexChanged.connect(self._on_res_changed)

        apply_res = _btn('Apply', self._apply_resolution, 80)
        self._res_status = _status(self)
        gl.addLayout(_row(QLabel('Resolution:'), 8, self._res_combo,
                          16, QLabel('Refresh:'), 8, self._hz_combo,
                          12, apply_res, None))
        gl.addWidget(self._res_status)
        root.addWidget(g)

        # Orientation group
        g2 = _section('Orientation')
        g2l = QVBoxLayout(g2)
        self._orient_combo = QComboBox()
        for k, v in DS.ORIENT_LABELS.items():
            self._orient_combo.addItem(v, k)
        cur_orient = info.get('orientation', 0)
        self._orient_combo.setCurrentIndex(cur_orient)
        apply_o = _btn('Apply', self._apply_orientation, 80)
        self._orient_status = _status(self)
        g2l.addLayout(_row(QLabel('Orientation:'), 8, self._orient_combo, 12, apply_o, None))
        g2l.addWidget(self._orient_status)
        root.addWidget(g2)

        root.addStretch()

    def _load(self):
        self._resolutions = DS.enum_resolutions()
        res_set = sorted({(w, h) for w, h, _ in self._resolutions}, reverse=True)
        self._res_combo.blockSignals(True)
        self._res_combo.clear()
        for w, h in res_set:
            self._res_combo.addItem(f'{w} × {h}', (w, h))
        self._res_combo.blockSignals(False)
        if res_set:
            self._on_res_changed(0)

    def _on_res_changed(self, _):
        wh = self._res_combo.currentData()
        if not wh:
            return
        w, h = wh
        hz_list = sorted({hz for rw, rh, hz in self._resolutions if rw == w and rh == h}, reverse=True)
        self._hz_combo.blockSignals(True)
        self._hz_combo.clear()
        for hz in hz_list:
            self._hz_combo.addItem(f'{hz} Hz', hz)
        self._hz_combo.blockSignals(False)

    def _apply_resolution(self):
        wh = self._res_combo.currentData()
        hz = self._hz_combo.currentData()
        if not wh or not hz:
            return
        ok, msg = DS.set_resolution(wh[0], wh[1], hz)
        self._res_status.setStyleSheet(f'color: {"#44aa44" if ok else "#cc4444"}; font-size: 10px;')
        self._res_status.setText(msg)

    def _apply_orientation(self):
        orient = self._orient_combo.currentData()
        ok, msg = DS.set_orientation(orient)
        self._orient_status.setStyleSheet(f'color: {"#44aa44" if ok else "#cc4444"}; font-size: 10px;')
        self._orient_status.setText(msg)


class _MousePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(400)
        self._size_timer.timeout.connect(self._do_set_cursor_size)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        # Speed
        g = _section('Pointer Speed')
        gl = QVBoxLayout(g)
        self._speed_lbl = QLabel(f'Speed: {DS.get_mouse_speed()}  (1 = slow, 20 = fast)')
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(1, 20)
        self._speed_slider.setValue(DS.get_mouse_speed())
        self._speed_slider.setTickInterval(1)
        self._speed_slider.valueChanged.connect(self._set_speed)
        gl.addWidget(self._speed_lbl)
        gl.addWidget(self._speed_slider)
        root.addWidget(g)

        # Options
        g2 = _section('Pointer Options')
        g2l = QVBoxLayout(g2)
        self._precision_cb = QCheckBox('Enhance pointer precision  (acceleration)')
        self._precision_cb.setChecked(DS.get_enhance_precision())
        self._precision_cb.toggled.connect(lambda v: DS.set_enhance_precision(v))
        g2l.addWidget(self._precision_cb)

        self._vanish_cb = QCheckBox('Hide pointer while typing')
        self._vanish_cb.setChecked(DS.get_mouse_vanish())
        self._vanish_cb.toggled.connect(lambda v: DS.set_mouse_vanish(v))
        g2l.addWidget(self._vanish_cb)

        self._sonar_cb = QCheckBox('Show pointer location when Ctrl is pressed  (sonar)')
        self._sonar_cb.setChecked(DS.get_mouse_sonar())
        self._sonar_cb.toggled.connect(lambda v: DS.set_mouse_sonar(v))
        g2l.addWidget(self._sonar_cb)

        trails = DS.get_mouse_trails()
        self._trails_cb = QCheckBox('Pointer trails')
        self._trails_cb.setChecked(trails > 0)
        self._trails_slider = QSlider(Qt.Horizontal)
        self._trails_slider.setRange(2, 7)
        self._trails_slider.setValue(max(2, trails))
        self._trails_slider.setEnabled(trails > 0)
        self._trails_cb.toggled.connect(self._on_trails_toggle)
        self._trails_slider.valueChanged.connect(lambda v: DS.set_mouse_trails(v))
        g2l.addLayout(_row(self._trails_cb, 12, QLabel('Length:'), 6, self._trails_slider, None))
        root.addWidget(g2)

        # Cursor appearance
        g3 = _section('Cursor Appearance')
        g3l = QVBoxLayout(g3)
        sz = DS.get_cursor_size()
        self._size_lbl = QLabel(f'Cursor Size: {sz}  (1 = default, 15 = largest)')
        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(1, 15)
        self._size_slider.setValue(sz)
        self._size_slider.valueChanged.connect(self._set_cursor_size)
        g3l.addWidget(self._size_lbl)
        g3l.addWidget(self._size_slider)

        self._scheme_combo = QComboBox()
        cur_scheme = DS.get_cursor_scheme()
        for s in DS.list_cursor_schemes():
            self._scheme_combo.addItem(s)
        idx = self._scheme_combo.findText(cur_scheme)
        if idx >= 0:
            self._scheme_combo.setCurrentIndex(idx)
        apply_s = _btn('Apply Scheme', self._apply_scheme, 110)
        self._scheme_status = _status(self)
        g3l.addLayout(_row(QLabel('Cursor Scheme:'), 8, self._scheme_combo, 8, apply_s, None))
        g3l.addWidget(self._scheme_status)
        root.addWidget(g3)
        root.addStretch()

    def _set_speed(self, v):
        DS.set_mouse_speed(v)
        self._speed_lbl.setText(f'Speed: {v}  (1 = slow, 20 = fast)')

    def _on_trails_toggle(self, checked):
        self._trails_slider.setEnabled(checked)
        DS.set_mouse_trails(self._trails_slider.value() if checked else 0)

    def _set_cursor_size(self, v):
        self._size_lbl.setText(f'Cursor Size: {v}  (1 = default, 15 = largest)')
        self._size_timer.start()  # debounce

    def _do_set_cursor_size(self):
        v = self._size_slider.value()
        w = _Worker(DS.set_cursor_size, v, parent=self)
        w.done.connect(lambda ok: self._scheme_status.setText(
            '' if ok else 'Size change may require sign out.'))
        w.start()

    def _apply_scheme(self):
        self._scheme_status.setText('Applying…')
        name = self._scheme_combo.currentText()
        w = _Worker(DS.set_cursor_scheme, name, parent=self)
        def _done(ok):
            self._scheme_status.setStyleSheet(
                f'color: {"#44aa44" if ok else "#cc4444"}; font-size: 10px;')
            self._scheme_status.setText('Scheme applied.' if ok else 'Failed to apply scheme.')
        w.done.connect(_done)
        w.start()


class _AudioPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[_Worker] = None
        # Debounce: wait 350 ms after slider stops before sending to system
        self._vol_timer = QTimer(self)
        self._vol_timer.setSingleShot(True)
        self._vol_timer.setInterval(350)
        self._vol_timer.timeout.connect(self._do_set_volume)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        g = _section('Master Volume')
        gl = QVBoxLayout(g)
        self._vol_lbl = QLabel('Volume: —')
        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.valueChanged.connect(self._on_vol_changed)
        self._mute_cb = QCheckBox('Mute')
        self._mute_cb.toggled.connect(self._on_mute_toggle)
        refresh_vol = _btn('↺', self._load_volume, 32)
        gl.addLayout(_row(self._vol_lbl, None, refresh_vol))
        gl.addWidget(self._vol_slider)
        gl.addWidget(self._mute_cb)
        self._vol_status = _status(self)
        gl.addWidget(self._vol_status)
        root.addWidget(g)

        g2 = _section('Audio Devices')
        g2l = QVBoxLayout(g2)
        self._devices_lbl = QLabel('Loading…')
        self._devices_lbl.setWordWrap(True)
        self._devices_lbl.setStyleSheet('font-size: 11px;')
        g2l.addWidget(self._devices_lbl)
        g2l.addWidget(_btn('Refresh Devices', self._load_devices, 130))
        root.addWidget(g2)
        root.addStretch()

        self._load_volume()
        self._load_devices()

    def _load_volume(self):
        self._vol_status.setText('Reading volume…')
        w = _Worker(DS.get_volume_state, parent=self)
        w.done.connect(self._on_vol_loaded)
        self._worker = w
        w.start()

    def _on_vol_loaded(self, state):
        if not isinstance(state, dict):
            self._vol_status.setText('Could not read audio state.')
            return
        vol  = state.get('volume', 50)
        mute = state.get('muted', False)
        self._vol_slider.blockSignals(True)
        self._vol_slider.setValue(vol)
        self._vol_slider.blockSignals(False)
        self._vol_lbl.setText(f'Volume: {vol}%')
        self._vol_status.setText('')
        self._mute_cb.blockSignals(True)
        self._mute_cb.setChecked(mute)
        self._mute_cb.blockSignals(False)

    def _on_vol_changed(self, v):
        # Update label immediately; debounce the actual system call
        self._vol_lbl.setText(f'Volume: {v}%')
        self._vol_timer.start()

    def _do_set_volume(self):
        v = self._vol_slider.value()
        self._vol_status.setText(f'Setting {v}%…')
        w = _Worker(DS.set_master_volume, v, parent=self)
        def _done(ok):
            self._vol_status.setText('Done.' if ok else 'Failed.')
        w.done.connect(_done)
        w.start()

    def _on_mute_toggle(self, muted: bool):
        w = _Worker(DS.set_mute, muted, parent=self)
        self._vol_status.setText('Muting…' if muted else 'Unmuting…')
        def _done(ok):
            self._vol_status.setText('Done.' if ok else 'Failed.')
        w.done.connect(_done)
        w.start()

    def _load_devices(self):
        w = _Worker(DS.get_audio_devices, parent=self)
        w.done.connect(self._on_devices_loaded)
        w.start()

    def _on_devices_loaded(self, devices):
        if isinstance(devices, list) and devices:
            lines = [f"• {d['name']}  [{d['status']}]" for d in devices]
            self._devices_lbl.setText('\n'.join(lines))
        else:
            self._devices_lbl.setText('No audio devices found.')


class _NvidiaPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[_Worker] = None
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._load_info)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        smi = DS.find_nvidia_smi()
        if not smi:
            lbl = QLabel('NVIDIA GPU or nvidia-smi not detected on this system.\n\n'
                         'Install NVIDIA drivers to enable this panel.')
            lbl.setStyleSheet('font-size: 12px;')
            lbl.setAlignment(Qt.AlignCenter)
            root.addWidget(lbl)
            return

        # Info card
        g = _section('GPU Information')
        gl = QVBoxLayout(g)
        self._info_lbl = QLabel('Loading…')
        self._info_lbl.setFont(QFont('Consolas', 9))
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet('')
        gl.addWidget(self._info_lbl)
        gl.addWidget(_btn('↺ Refresh', self._load_info, 90))
        root.addWidget(g)

        # Performance bars
        g2 = _section('Live Utilization')
        g2l = QVBoxLayout(g2)
        self._gpu_bar = QProgressBar()
        self._gpu_bar.setRange(0, 100)
        self._gpu_bar.setTextVisible(True)
        self._gpu_bar.setFormat('0%')
        self._mem_bar = QProgressBar()
        self._mem_bar.setRange(0, 100)
        self._mem_bar.setTextVisible(True)
        self._mem_bar.setFormat('0%')
        g2l.addLayout(_row(QLabel('GPU:'), 6, self._gpu_bar))
        g2l.addLayout(_row(QLabel('VRAM:'), 4, self._mem_bar))
        root.addWidget(g2)

        # Settings
        g3 = _section('NVIDIA Settings')
        g3l = QVBoxLayout(g3)
        self._shader_cb = QCheckBox('Shader Disk Cache  (improves load times)')
        self._shader_cb.setChecked(DS.get_nvidia_shader_cache())
        self._shader_cb.toggled.connect(lambda v: DS.set_nvidia_shader_cache(v))
        g3l.addWidget(self._shader_cb)

        open_cp = _btn('Open NVIDIA Control Panel', DS.open_nvidia_control_panel, 220)
        g3l.addWidget(open_cp)
        root.addWidget(g3)
        root.addStretch()

        self._load_info()
        self._timer.start()

    def _load_info(self):
        if self._worker and self._worker.isRunning():
            return
        self._worker = _Worker(DS.get_nvidia_info, parent=self)
        self._worker.done.connect(self._on_info)
        self._worker.start()

    def _on_info(self, info):
        if not isinstance(info, dict):
            self._info_lbl.setText('Could not read GPU info.')
            return
        _fan_str = info.get('fan', '')
        _fan = f'\nFan:      {_fan_str}' if _fan_str else ''
        self._info_lbl.setText(
            f"GPU:      {info['name']}\n"
            f"Driver:   {info['driver']}\n"
            f"Temp:     {info['temp']} °C{_fan}\n"
            f"GPU Use:  {info['gpu_util']} %\n"
            f"VRAM:     {info['mem_used']} / {info['mem_total']} MiB  ({info['mem_util']} %)\n"
            f"Power:    {info['power_draw']} W  /  {info['power_limit']} W  limit\n"
            f"Clocks:   GPU {info['clock_gpu']} MHz   MEM {info['clock_mem']} MHz"
        )
        try:
            gpu_util = int(float(info['gpu_util']))
            self._gpu_bar.setValue(gpu_util)
            self._gpu_bar.setFormat(f"{gpu_util}%  |  {info['temp']} C")
            used  = int(float(info['mem_used']))
            total = int(float(info['mem_total']))
            mem_pct = int(used * 100 / total) if total else 0
            self._mem_bar.setValue(mem_pct)
            self._mem_bar.setFormat(f"{mem_pct}%  ({info['mem_used']} / {info['mem_total']} MiB)")
        except Exception:
            pass


class _PowerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        # Power plans
        g = _section('Power Plan')
        gl = QVBoxLayout(g)
        self._plan_combo = QComboBox()
        self._plan_status = _status(self)
        plans = DS.get_power_plans()
        active = DS.get_active_power_plan()
        for guid, name in plans:
            self._plan_combo.addItem(name or guid, guid)
            if guid == active:
                self._plan_combo.setCurrentIndex(self._plan_combo.count() - 1)
        apply_plan = _btn('Apply', self._apply_plan, 80)
        gl.addLayout(_row(QLabel('Select:'), 8, self._plan_combo, 8, apply_plan, None))
        gl.addWidget(self._plan_status)
        root.addWidget(g)

        # Toggles — checkable QPushButtons so they look like real buttons in all themes
        g2 = _section('Sleep & Startup')
        g2l = QVBoxLayout(g2)

        def _toggle_btn(label: str, checked: bool) -> QPushButton:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(checked)
            b.setMinimumHeight(30)
            b.setStyleSheet(
                'QPushButton { text-align: left; padding: 4px 10px; }'
                'QPushButton:checked { background: #2d5a8e; color: white; }'
            )
            return b

        self._hibernate_btn = _toggle_btn(
            'Hibernation  —  saves full memory to disk on sleep',
            DS.get_hibernate())
        self._hibernate_btn.toggled.connect(self._set_hibernate)

        self._faststartup_btn = _toggle_btn(
            'Fast Startup  —  hybrid shutdown, faster boot (may cause issues)',
            DS.get_fast_startup())
        self._faststartup_btn.toggled.connect(lambda v: DS.set_fast_startup(v))

        self._usb_btn = _toggle_btn(
            'USB Selective Suspend  —  saves power (may cause USB drops)',
            DS.get_usb_selective_suspend())
        self._usb_btn.toggled.connect(lambda v: DS.set_usb_selective_suspend(v))

        self._power_status = _status(self)
        for btn in (self._hibernate_btn, self._faststartup_btn, self._usb_btn):
            g2l.addWidget(btn)
        g2l.addWidget(self._power_status)
        root.addWidget(g2)

        # Fan Status
        g3 = _section('Fan Status')
        g3l = QVBoxLayout(g3)
        self._fan_lbl = QLabel('Detecting fans…')
        self._fan_lbl.setWordWrap(True)
        self._fan_lbl.setStyleSheet('font-size: 11px;')
        g3l.addWidget(self._fan_lbl)
        g3l.addWidget(_btn('Refresh', self._load_fans, 80))
        root.addWidget(g3)

        # Administrator Mode
        g4 = _section('Administrator Mode')
        g4l = QVBoxLayout(g4)
        admin_running = DS.is_admin()
        self._admin_status_lbl = QLabel(
            'Status: Running as Administrator' if admin_running
            else 'Status: Standard user  —  some settings above may fail'
        )
        self._admin_status_lbl.setStyleSheet(
            f'color: {"#44aa44" if admin_running else "#cc9900"}; font-size: 11px;'
        )
        g4l.addWidget(self._admin_status_lbl)
        if not admin_running:
            self._admin_btn = _btn('Restart as Administrator', self._restart_admin, 200)
            g4l.addWidget(self._admin_btn)
        root.addWidget(g4)

        root.addStretch()

        self._load_fans()

    def _load_fans(self):
        self._fan_lbl.setText('Detecting fans…')
        w = _Worker(DS.get_fan_info, parent=self)
        w.done.connect(self._on_fan_info)
        w.start()

    def _on_fan_info(self, info):
        if not isinstance(info, dict):
            self._fan_lbl.setText('Fan data unavailable.')
            return
        lines = []
        if info.get('gpu_fan_pct') is not None:
            lines.append(f'GPU Fan:  {info["gpu_fan_pct"]}%')
        elif info.get('gpu_fan_na'):
            lines.append('GPU Fan:  N/A  (not exposed by NVIDIA driver on this laptop)')
        for fan in info.get('ohm_fans', []):
            lines.append(f'{fan["name"]}:  {fan["rpm"]} RPM  (via OpenHardwareMonitor)')
        for fan in info.get('cpu_fans', []):
            lines.append(f'{fan.get("name","Fan")}:  {fan.get("rpm",0)} RPM')
        if lines:
            self._fan_lbl.setText('\n'.join(lines))
        else:
            self._fan_lbl.setText(
                'No fan data available.\n'
                'GPU fan speed not exposed by driver.\n'
                'Install OpenHardwareMonitor and run it as a Windows service\n'
                'to enable CPU / system fan speed monitoring.'
            )

    def _restart_admin(self):
        ok = DS.restart_as_admin()
        if ok:
            self._power_status.setStyleSheet('color: #44aa44; font-size: 10px;')
            self._power_status.setText('Relaunching as Administrator — closing in 2 s…')
            QTimer.singleShot(2000, self._do_quit)
        else:
            self._power_status.setStyleSheet('color: #cc4444; font-size: 10px;')
            self._power_status.setText('Elevation denied or already running as Administrator.')

    @staticmethod
    def _do_quit():
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def _apply_plan(self):
        guid = self._plan_combo.currentData()
        name = self._plan_combo.currentText()
        ok = DS.set_power_plan(guid)
        if ok:
            self._plan_status.setStyleSheet('color: #44aa44; font-size: 10px;')
            self._plan_status.setText(f'Active plan: {name}')
        else:
            self._plan_status.setStyleSheet('color: #cc4444; font-size: 10px;')
            self._plan_status.setText('Failed — try running as Administrator.')

    def _set_hibernate(self, v):
        ok = DS.set_hibernate(v)
        if not ok:
            self._power_status.setStyleSheet('color: #cc4444; font-size: 10px;')
            self._power_status.setText('Hibernation change requires Administrator.')
            self._hibernate_btn.blockSignals(True)
            self._hibernate_btn.setChecked(not v)
            self._hibernate_btn.blockSignals(False)


class _PrivacyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _toggle(self, key: str, val: bool, status: QLabel):
        ok = DS.set_privacy_setting(key, val)
        status.setStyleSheet(f'color: {"#44aa44" if ok else "#cc4444"}; font-size: 10px;')
        status.setText('Applied.' if ok else 'Failed — may need Administrator.')

    def _cb(self, key: str, label: str, status: QLabel) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(DS.get_privacy_setting(key))
        cb.toggled.connect(lambda v, k=key, s=status: self._toggle(k, v, s))
        return cb

    def _build(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignTop)
        root.setSpacing(10)

        note = QLabel('⚠  Some settings require Administrator — the app will attempt to apply them.\n'
                      '    Changes to services / policies take effect after signing out or reboot.')
        note.setStyleSheet('font-size: 10px; padding: 4px;')
        note.setWordWrap(True)
        root.addWidget(note)

        s1 = _status(self)

        g = _section('Microsoft Telemetry & Data Collection')
        gl = QVBoxLayout(g)
        gl.addWidget(self._cb('advertising_id',  'Advertising ID  (personalised ads in apps)',   s1))
        gl.addWidget(self._cb('telemetry',        'Diagnostic & usage data  (telemetry)',         s1))
        gl.addWidget(self._cb('error_reporting',  'Windows Error Reporting',                       s1))
        gl.addWidget(self._cb('delivery_optimization', 'Delivery Optimisation  (share Windows updates with other PCs on internet)', s1))
        gl.addWidget(s1)
        root.addWidget(g)

        s2 = _status(self)
        g2 = _section('Search & Cortana')
        g2l = QVBoxLayout(g2)
        g2l.addWidget(self._cb('web_search_start', 'Bing web search results in Start menu',      s2))
        g2l.addWidget(self._cb('cortana',          'Cortana voice assistant',                    s2))
        g2l.addWidget(self._cb('activity_history', 'Timeline / Activity History',                s2))
        g2l.addWidget(s2)
        root.addWidget(g2)

        s3 = _status(self)
        g3 = _section('Gaming & Background')
        g3l = QVBoxLayout(g3)
        g3l.addWidget(self._cb('game_dvr',        'Game DVR / Game Clips  (Xbox Game Bar recording)', s3))
        g3l.addWidget(self._cb('game_bar',        'Game Mode  (auto game mode)',                  s3))
        g3l.addWidget(self._cb('background_apps', 'Background apps  (all UWP apps)',              s3))
        g3l.addWidget(s3)
        root.addWidget(g3)

        s4 = _status(self)
        g4 = _section('Interface & Suggestions')
        g4l = QVBoxLayout(g4)
        g4l.addWidget(self._cb('windows_tips',   'Windows tips and suggestions',                 s4))
        g4l.addWidget(self._cb('lock_screen_ads','Lock screen spotlight / ads',                  s4))
        g4l.addWidget(self._cb('autoplay',       'AutoPlay  (auto-run when media inserted)',     s4))
        g4l.addWidget(self._cb('numlock_startup','NumLock on at login screen',                   s4))
        g4l.addWidget(self._cb('fast_user_switching', 'Fast User Switching  (Switch User button)', s4))
        g4l.addWidget(s4)
        root.addWidget(g4)

        s5 = _status(self)
        g5 = _section('Performance Services')
        g5l = QVBoxLayout(g5)

        self._superfetch_cb = QCheckBox('SysMain  (Superfetch — preloads apps; disable on SSD+16GB+ RAM)')
        self._superfetch_cb.setChecked(DS.get_superfetch())
        self._superfetch_cb.toggled.connect(lambda v: self._apply_service(DS.set_superfetch, v, s5))
        g5l.addWidget(self._superfetch_cb)

        self._memcomp_cb = QCheckBox('Memory Compression  (compresses RAM pages; disable for low-latency workloads)')
        self._memcomp_cb.setChecked(DS.get_memory_compression())
        self._memcomp_cb.toggled.connect(lambda v: self._apply_service(DS.set_memory_compression, v, s5))
        g5l.addWidget(self._memcomp_cb)
        g5l.addWidget(s5)
        root.addWidget(g5)
        root.addStretch()

    def _apply_service(self, fn, val, status: QLabel):
        w = _Worker(fn, val, parent=self)
        status.setText('Applying…')
        def _done(ok):
            status.setStyleSheet(f'color: {"#44aa44" if ok else "#cc4444"}; font-size: 10px;')
            status.setText('Applied.' if ok else 'Failed — run as Administrator.')
        w.done.connect(_done)
        w.start()


# ── main widget ───────────────────────────────────────────────────────────────

class DeviceSettingsView(QWidget):
    _PAGES = [
        'Display',
        'Mouse & Cursor',
        'Sound',
        'NVIDIA',
        'Power',
        'Privacy & Hidden',
    ]

    # Per-theme sidebar QSS — mirrors QTabBar::tab colors from main_window.py
    _SIDEBAR_QSS: Dict[str, str] = {
        'dark': (
            'QListWidget { background: #0a0a0a; border: none; border-right: 1px solid #1a3a1a;'
            '              font-size: 12px; color: #4a7a4a; }'
            'QListWidget::item { padding: 10px 14px; }'
            'QListWidget::item:selected { background: #000000; color: #00ff41;'
            '    border-left: 3px solid #00ff41; }'
            'QListWidget::item:hover:!selected { background: #0d1f0d; color: #00cc33; }'
        ),
        'dark_bw': (
            'QListWidget { background: #0d0d0d; border: none; border-right: 1px solid #333333;'
            '              font-size: 12px; color: #888888; }'
            'QListWidget::item { padding: 10px 14px; }'
            'QListWidget::item:selected { background: #000000; color: #ffffff;'
            '    border-left: 3px solid #ffffff; }'
            'QListWidget::item:hover:!selected { background: #1a1a1a; color: #cccccc; }'
        ),
        'light': (
            'QListWidget { background: #b0b0b0; border: none; border-right: 1px solid #808080;'
            '              font-size: 12px; color: #444444; }'
            'QListWidget::item { padding: 10px 14px; }'
            'QListWidget::item:selected { background: #c0c0c0; color: #000000;'
            '    border-left: 3px solid #000000; }'
            'QListWidget::item:hover:!selected { background: #aaaaaa; color: #000000; }'
        ),
        'hc': (
            'QListWidget { background: #000000; border: none; border-right: 2px solid #ffffff;'
            '              font-size: 12px; color: #ffffff; }'
            'QListWidget::item { padding: 10px 14px; }'
            'QListWidget::item:selected { background: #000000; color: #ffff00;'
            '    border-left: 3px solid #ffff00; }'
            'QListWidget::item:hover:!selected { background: #222222; color: #ffffff; }'
        ),
        'win95': (
            'QListWidget { background: #c0c0c0; border: none; border-right: 1px solid #808080;'
            '              font-size: 12px; color: #000000; }'
            'QListWidget::item { padding: 9px 14px; border-bottom: 1px solid #808080; }'
            'QListWidget::item:selected { background: #c0c0c0; color: #000000; font-weight: bold;'
            '    border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;'
            '    border-right: 2px solid #808080; }'
            'QListWidget::item:hover:!selected { background: #d0d0d0; }'
        ),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: list = []
        self._build()

    def set_theme(self, theme: str):
        qss = self._SIDEBAR_QSS.get(theme, self._SIDEBAR_QSS['dark'])
        self._sidebar.setStyleSheet(qss)

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar — starts with dark theme; set_theme() updates it when theme changes
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(150)
        self._sidebar.setStyleSheet(self._SIDEBAR_QSS['dark'])
        for name in self._PAGES:
            item = QListWidgetItem(f'  {name}')
            self._sidebar.addItem(item)

        # Stack
        self._stack = QStackedWidget()

        page_classes = [_DisplayPage, _MousePage, _AudioPage,
                        _NvidiaPage, _PowerPage, _PrivacyPage]
        for cls in page_classes:
            page = cls(self)
            scroll = _scroll(page)
            scroll.setContentsMargins(8, 8, 8, 8)
            self._stack.addWidget(scroll)
            self._pages.append(page)

        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._sidebar.setCurrentRow(0)

        root.addWidget(self._sidebar)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        div.setStyleSheet('color: #333333;')
        root.addWidget(div)

        root.addWidget(self._stack, 1)
