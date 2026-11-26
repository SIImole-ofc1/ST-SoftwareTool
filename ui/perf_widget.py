"""Desktop Performance widget (macOS).

A small macOS-style widget that sits on the desktop — above the wallpaper and desktop icons,
below every window, on all Spaces — and shows live CPU, RAM, GPU, SSD and network use.

It is an ST-SoftwareTool window, not a WidgetKit gallery widget: gallery widgets may only
refresh every 15+ minutes, this one updates every second. It is shown while the app runs
(including when it keeps running in the background).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from core.osinfo import IS_MAC

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

_W        = 300
_PAD      = 16
_HEADER_H = 30
_ROW_H    = 26
_NET_H    = 22
_RADIUS   = 22          # macOS widget corner radius
_ACCENT   = {'CPU': '#0A84FF', 'RAM': '#30D158', 'GPU': '#FF9F0A', 'SSD': '#BF5AF2'}

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'assets', 'STsoftwareterminalLOGO.png.png')


def _disk_percent() -> Optional[float]:
    """Used space of the main disk. On APFS the Data volume reports the whole SSD."""
    if psutil is None:
        return None
    for path in ('/System/Volumes/Data', '/'):
        if os.path.isdir(path):
            try:
                return float(psutil.disk_usage(path).percent)
            except OSError:
                continue
    return None


def _fmt_rate(kb_s: float) -> str:
    return f'{kb_s / 1024:.1f} MB/s' if kb_s >= 1024 else f'{kb_s:.0f} KB/s'


def pin_to_desktop(widget: QWidget) -> Optional[int]:
    """macOS: move the widget's window to desktop level (just above the desktop icons, below
    all windows), keep it on every Space and out of Mission Control / window cycling.
    Uses the Objective-C runtime through ctypes. Returns the window level now in effect."""
    if not IS_MAC:
        return None
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
    except (OSError, TypeError):
        return None
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def send(restype, *argtypes):
        # objc_msgSend called through an exact prototype (required on Apple Silicon)
        return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)

    sel = objc.sel_registerName
    window = send(ctypes.c_void_p)(ctypes.c_void_p(int(widget.winId())), sel(b'window'))
    if not window:
        return None
    try:
        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library('CoreGraphics'))
        cg.CGWindowLevelForKey.restype = ctypes.c_int32
        cg.CGWindowLevelForKey.argtypes = [ctypes.c_int32]
        icon_level = cg.CGWindowLevelForKey(18)        # kCGDesktopIconWindowLevelKey
    except (OSError, TypeError, AttributeError):
        icon_level = -2147483603                        # its documented value
    send(None, ctypes.c_long)(window, sel(b'setLevel:'), icon_level + 1)
    # CanJoinAllSpaces | Stationary | IgnoresCycle
    send(None, ctypes.c_ulong)(window, sel(b'setCollectionBehavior:'), 1 | 16 | 64)
    return int(send(ctypes.c_long)(window, sel(b'level')))


class PerfWidget(QWidget):
    open_requested   = Signal()          # double-click / "Open ST-SoftwareTool"
    remove_requested = Signal()          # "Remove Widget"
    moved            = Signal(int, int)  # dropped at a new position

    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setWindowTitle('ST-SoftwareTool Performance')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)   # never keeps the app alive on its own
        self.setToolTip('ST-SoftwareTool Performance\n'
                        'Drag to move · double-click to open the app · right-click for options')

        self._cpu: Optional[float] = None
        self._ram: Optional[float] = None
        self._ram_text = ''
        self._gpu: Optional[float] = None
        self._disk: Optional[float] = _disk_percent()
        self._up = self._down = 0.0
        self._drag: Optional[QPoint] = None
        self.level: Optional[int] = None

        logo = QPixmap(_LOGO)
        self._logo = (logo.scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                      if not logo.isNull() else None)

        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(30_000)
        self._disk_timer.timeout.connect(self._refresh_disk)
        self._disk_timer.start()

        try:   # follow the system's light / dark appearance, like macOS widgets do
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.update())
        except AttributeError:
            pass
        self._fit_height()

    # ── data in ───────────────────────────────────────────────────────────────

    def set_perf(self, data: dict) -> None:
        """Slot for the app's once-a-second performance sample."""
        self._cpu = float(data['cpu'])
        used, total, pct = data['ram']
        self._ram = float(pct)
        self._ram_text = f'{used:.1f} / {total:.0f} GB'
        self._up, self._down = data['net']
        self.update()

    def set_gpu(self, value: float) -> None:
        """Slot for the GPU sample (every ~3 s); a negative value means not available."""
        gpu = None if value is None or value < 0 else float(value)
        had_row = self._gpu is not None
        self._gpu = gpu
        if had_row != (gpu is not None):
            self._fit_height()
        self.update()

    def _refresh_disk(self) -> None:
        self._disk = _disk_percent()
        self.update()

    def _rows(self):
        rows = [('CPU', self._cpu), ('RAM', self._ram)]
        if self._gpu is not None:
            rows.append(('GPU', self._gpu))
        if self._disk is not None:
            rows.append(('SSD', self._disk))
        return rows

    def _fit_height(self) -> None:
        self.setFixedSize(_W, _PAD + _HEADER_H + len(self._rows()) * _ROW_H + _NET_H + _PAD)

    # ── placement ─────────────────────────────────────────────────────────────

    def place(self, pos) -> None:
        """Move to a saved [x, y] if it is still on a screen, else top-right of the main screen."""
        if pos and len(pos) == 2:
            point = QPoint(int(pos[0]), int(pos[1]))
            if QGuiApplication.screenAt(point + QPoint(_W // 2, 20)) is not None:
                self.move(point)
                return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - _W - 24, area.top() + 24)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._pin)    # after Qt has set up the native window

    def _pin(self) -> None:
        try:
            self.level = pin_to_desktop(self)
        except Exception:
            self.level = None

    # ── drawing ───────────────────────────────────────────────────────────────

    @staticmethod
    def _is_dark() -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except AttributeError:
            return True

    def paintEvent(self, _event) -> None:
        dark = self._is_dark()
        bg     = QColor(28, 28, 30, 218) if dark else QColor(246, 246, 248, 228)
        border = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 28)
        text   = QColor('#f5f5f7') if dark else QColor('#1d1d1f')
        sub    = QColor(235, 235, 245, 160) if dark else QColor(60, 60, 67, 165)
        track  = QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 24)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS)
        p.fillPath(shape, bg)
        p.setPen(border)
        p.drawPath(shape)

        base = QFont(self.font())
        x, y, right = _PAD, _PAD, _W - _PAD

        # Header: logo + title, RAM in GB on the right
        tx = x
        if self._logo is not None:
            p.drawPixmap(x, y + 4, self._logo)
            tx = x + 26
        title = QFont(base)
        title.setPointSizeF(13)
        title.setBold(True)
        p.setFont(title)
        p.setPen(text)
        p.drawText(QRectF(tx, y, right - tx, 26), Qt.AlignVCenter | Qt.AlignLeft, 'Performance')
        small = QFont(base)
        small.setPointSizeF(10.5)
        p.setFont(small)
        p.setPen(sub)
        p.drawText(QRectF(tx, y, right - tx, 26), Qt.AlignVCenter | Qt.AlignRight, self._ram_text)
        y += _HEADER_H

        # Bars
        label_font = QFont(base)
        label_font.setPointSizeF(11)
        label_font.setBold(True)
        value_font = QFont(base)
        value_font.setPointSizeF(11.5)
        bar_x, bar_right = x + 40, right - 46
        for name, value in self._rows():
            p.setFont(label_font)
            p.setPen(sub)
            p.drawText(QRectF(x, y, 40, _ROW_H), Qt.AlignVCenter | Qt.AlignLeft, name)
            bar = QRectF(bar_x, y + (_ROW_H - 8) / 2, bar_right - bar_x, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(bar, 4, 4)
            if value is not None and value > 0:
                fill = QRectF(bar.x(), bar.y(), max(8.0, bar.width() * min(value, 100.0) / 100.0), 8)
                p.setBrush(QColor(_ACCENT[name]))
                p.drawRoundedRect(fill, 4, 4)
            p.setFont(value_font)
            p.setPen(text)
            p.drawText(QRectF(bar_right, y, right - bar_right, _ROW_H), Qt.AlignVCenter | Qt.AlignRight,
                       '—' if value is None else f'{value:.0f}%')
            y += _ROW_H

        # Network
        p.setFont(small)
        p.setPen(sub)
        p.drawText(QRectF(x, y, right - x, _NET_H), Qt.AlignVCenter | Qt.AlignLeft,
                   f'↑ {_fmt_rate(self._up)}     ↓ {_fmt_rate(self._down)}')
        p.end()

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and event.button() == Qt.LeftButton:
            self._drag = None
            self.moved.emit(self.x(), self.y())

    def mouseDoubleClickEvent(self, _event) -> None:
        self.open_requested.emit()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        open_action = menu.addAction('Open ST-SoftwareTool')
        menu.addSeparator()
        remove_action = menu.addAction('Remove Widget')
        chosen = menu.exec(event.globalPos())
        if chosen is open_action:
            self.open_requested.emit()
        elif chosen is remove_action:
            self.remove_requested.emit()
