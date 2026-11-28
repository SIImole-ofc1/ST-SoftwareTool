"""Desktop widgets (macOS): Performance, Clock, Network, and Music & Audio.

They look and behave like Apple's desktop widgets: the same sizes (small 164 pt, medium 344 x
164 pt cards inside 180 pt grid cells), they snap to the same grid (8 pt from the screen edge,
180 pt apart) when dropped, sit on the desktop below every window on all Spaces, use the glass
look from Settings › Glass Look, and follow light / dark mode. A click opens ST-SoftwareTool;
a drag moves the widget; right-click for options.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections import deque
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QMenu, QWidget

from core import mac_appkit

CELL = 180          # Apple's grid pitch (and small widget frame)
INSET = 8           # transparent margin around the card (room for the shadow)
EDGE = 8            # grid distance from the screen edges
RADIUS = 22
SIZES = {'small': (CELL, CELL), 'medium': (2 * CELL, CELL), 'large': (2 * CELL, 2 * CELL)}
KINDS = ('performance', 'clock', 'network', 'music')
TITLES = {'performance': 'Performance', 'clock': 'Clock', 'network': 'Network',
          'music': 'Music & Audio'}
_ACCENT = {'CPU': '#0A84FF', 'RAM': '#30D158', 'GPU': '#FF9F0A', 'SSD': '#BF5AF2'}
_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'assets', 'STsoftwareterminalLOGO.png.png')


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return True


from core.performance_monitor import fmt_rate as _fmt_rate      # noqa: E402


# ── the grid ─────────────────────────────────────────────────────────────────

def apple_widget_rects() -> list:
    """Where macOS's own desktop widgets are (window frames, in points). Only positions and
    sizes are read — nothing of what they show."""
    script = ('ObjC.import("CoreGraphics");'
              'const l = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo('
              '$.kCGWindowListOptionAll, 0))) || [];'
              'JSON.stringify(l.filter(w => w.kCGWindowOwnerName === "Notification Center" && '
              '(w.kCGWindowLayer || 0) < 0 && w.kCGWindowBounds && w.kCGWindowBounds.Width > 100'
              ' && w.kCGWindowBounds.Width < 800).map(w => w.kCGWindowBounds))')
    try:
        out = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script],
                             capture_output=True, text=True, timeout=3).stdout
        return [QRect(int(b['X']), int(b['Y']), int(b['Width']), int(b['Height']))
                for b in json.loads(out or '[]')]
    except Exception:
        return []


def snap(pos: QPoint, size: tuple, occupied: list) -> QPoint:
    """The nearest free cell of Apple's desktop widget grid for a frame of `size`."""
    w, h = size
    screen = QGuiApplication.screenAt(pos + QPoint(w // 2, h // 2)) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    left = area.left() + EDGE
    right = area.right() + 1 - EDGE
    top = area.top() + EDGE
    # Like macOS: columns on the left half count from the left edge, on the right half from
    # the right edge (so widgets line up with Apple's on either side)
    mid = (area.left() + area.right()) / 2
    xs = set()
    k = 0
    while left + k * CELL + w <= right:
        if left + k * CELL + w / 2 < mid:
            xs.add(left + k * CELL)
        if right - w - k * CELL + w / 2 >= mid:
            xs.add(right - w - k * CELL)
        k += 1
    ys = [top + r * CELL for r in range(max(1, (area.bottom() - top) // CELL + 1))
          if top + r * CELL + h <= area.bottom() + 1]
    cells = [QPoint(x, y) for x in xs for y in ys] or [pos]
    cells.sort(key=lambda c: (c - pos).manhattanLength())
    for c in cells:
        if not any(QRect(c, QPoint(c.x() + w - 1, c.y() + h - 1)).intersects(r.adjusted(4, 4, -4, -4))
                   for r in occupied):
            return c
    return cells[0]


# ── the base widget ──────────────────────────────────────────────────────────

class DesktopWidget(QWidget):
    """A glass card on the desktop. Subclasses draw their content in paint_content()."""
    kind = ''
    size_class = 'medium'
    open_requested   = Signal()
    remove_requested = Signal(str)
    edit_requested   = Signal()
    moved            = Signal(str, int, int)
    lock_toggled     = Signal(bool)            # "Lock Widgets in Place" from the menu
    opens_app = True                           # a click on the widget opens ST-SoftwareTool
    _CLICK_SLOP = 4

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint
                            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setWindowTitle(f'ST-SoftwareTool {TITLES.get(self.kind, "")}')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setFixedSize(*SIZES[self.size_class])
        self.level: Optional[int] = None
        self.snap_enabled = True
        self.locked = False                             # True: can't be dragged
        self._update_hints()
        self.occupied = lambda: []                      # other widgets' frames (set by the owner)
        self._press: Optional[QPoint] = None
        self._drag: Optional[QPoint] = None
        self._moved = False
        self._gloss = 0.55
        self._tint = 218 / 255
        self._blur = 80
        from ui.glass import rounded_mask_png
        self.backdrop = mac_appkit.BlurBackdrop(
            material='hud', mask_png=rounded_mask_png(RADIUS, margin=INSET),
            cap_inset=RADIUS + INSET + 1)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.update())
        except AttributeError:
            pass

    # placement ---------------------------------------------------------------

    def card(self) -> QRectF:
        return QRectF(self.rect()).adjusted(INSET, INSET, -INSET, -INSET)

    def place(self, pos) -> None:
        """A saved [x, y] if it's still on a screen, else a free grid cell top-right."""
        if pos and len(pos) == 2:
            point = QPoint(int(pos[0]), int(pos[1]))
            if QGuiApplication.screenAt(point + QPoint(self.width() // 2, 20)) is not None:
                self.move(point)
                return
        area = QGuiApplication.primaryScreen().availableGeometry()
        start = QPoint(area.right() - self.width(), area.top())
        self.move(snap(start, (self.width(), self.height()), self.occupied() + apple_widget_rects()))

    def setVisible(self, visible: bool) -> None:
        # macOS nudges a new borderless window when it first appears: put it back exactly
        # where it was placed (on the grid) right after
        target = self.pos()
        super().setVisible(visible)
        if visible:
            QTimer.singleShot(0, lambda: self.move(target) if self.pos() != target else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._pin)

    def _pin(self) -> None:
        self.level = mac_appkit.pin_to_desktop(int(self.winId()))
        self._apply_backdrop()

    def dispose(self) -> None:
        self.close()

    # glass -------------------------------------------------------------------

    def set_glass(self, gloss: int, blur: int) -> None:
        self._gloss = max(0, min(100, int(gloss))) / 100.0
        self._blur = max(0, min(100, int(blur)))
        if self.isVisible():
            self._apply_backdrop()
        self._refresh_tint()

    def _apply_backdrop(self) -> None:
        from ui import glass
        if self.backdrop.attach(int(self.winId())):
            self.backdrop.update(True, glass.blur_alpha(self._blur), dark=None)
        self._refresh_tint()

    def _refresh_tint(self) -> None:
        from ui import glass
        self._tint = (0.28 + 0.58 * (1.0 - glass.blur_alpha(self._blur))
                      if self.backdrop.attached else 218 / 255)
        self.update()

    # painting ----------------------------------------------------------------

    def colors(self, dark: bool) -> dict:
        return {
            'text': QColor('#f5f5f7') if dark else QColor('#1d1d1f'),
            'sub': QColor(235, 235, 245, 160) if dark else QColor(60, 60, 67, 165),
            'track': QColor(255, 255, 255, 34) if dark else QColor(0, 0, 0, 24),
            'accent': QColor(mac_appkit.accent_color() or '#0A84FF'),
        }

    def paintEvent(self, _event) -> None:
        dark = is_dark()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = self.card()
        shape = QPainterPath()
        shape.addRoundedRect(card, RADIUS, RADIUS)
        # soft shadow, like Apple's widgets
        for i, a in enumerate((26, 16, 9, 4)):
            sh = QPainterPath()
            sh.addRoundedRect(card.adjusted(-i, -i + 2, i, i + 3), RADIUS + i, RADIUS + i)
            p.fillPath(sh, QColor(0, 0, 0, a))
        bg = QColor(28, 28, 30) if dark else QColor(246, 246, 248)
        bg.setAlpha(round(255 * self._tint))
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillPath(shape, QColor(0, 0, 0, 0))            # the blur shows through the card
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.fillPath(shape, bg)
        g = self._gloss
        if g:
            sheen = QLinearGradient(card.topLeft(), QPointF(card.left(), card.top() + card.height() * 0.62))
            sheen.setColorAt(0.0, QColor(255, 255, 255, round((60 if dark else 150) * g)))
            sheen.setColorAt(0.45, QColor(255, 255, 255, round((16 if dark else 50) * g)))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillPath(shape, sheen)
        rim = QLinearGradient(card.topLeft(), card.bottomLeft())
        rim.setColorAt(0.0, QColor(255, 255, 255, round(40 + 160 * g)))
        rim.setColorAt(0.35, QColor(255, 255, 255, round(22 + 30 * g) if dark else round(50 * g)))
        rim.setColorAt(1.0, QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 30))
        p.setPen(QPen(rim, 1.0))
        p.drawPath(shape)
        p.setClipPath(shape)
        self.paint_content(p, card, dark, self.colors(dark))
        p.end()

    def paint_content(self, p: QPainter, card: QRectF, dark: bool, c: dict) -> None:
        pass

    def _f(self, size: float, weight=QFont.Normal) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(size)
        f.setWeight(weight)
        return f

    # mouse -------------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self._update_hints()

    def _update_hints(self) -> None:
        hints = (['Click to open ST-SoftwareTool'] if self.opens_app else []) + \
                ([] if self.locked else ['drag to move']) + ['right-click for options']
        text = ' · '.join(hints)
        self.setToolTip(f'{TITLES.get(self.kind, "")}\n' + text[:1].upper() + text[1:])
        self.setCursor(Qt.PointingHandCursor if self.opens_app else Qt.ArrowCursor)

    def click(self, pos: QPointF) -> bool:
        """A click inside the widget. Return True if handled (e.g. a button); else the app
        opens."""
        return False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._drag = self._press - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None or self.locked or not event.buttons() & Qt.LeftButton:
            return
        pos = event.globalPosition().toPoint()
        if not self._moved and (pos - self._press).manhattanLength() < self._CLICK_SLOP:
            return
        self._moved = True
        self.setCursor(Qt.ClosedHandCursor)
        self.move(pos - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None or event.button() != Qt.LeftButton:
            return
        self._drag = None
        self._update_hints()
        if self._moved:
            if self.snap_enabled:
                self.move(snap(self.pos(), (self.width(), self.height()),
                               self.occupied() + apple_widget_rects()))
            self.moved.emit(self.kind, self.x(), self.y())
        elif not self.click(event.position()) and self.opens_app:
            self.open_requested.emit()

    def mouseDoubleClickEvent(self, _event) -> None:
        pass

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        open_action = menu.addAction('Open ST-SoftwareTool')
        edit_action = menu.addAction('Edit Widgets…')
        lock_action = menu.addAction('Lock Widgets in Place')
        lock_action.setCheckable(True)
        lock_action.setChecked(self.locked)
        self.extra_menu(menu)
        menu.addSeparator()
        remove_action = menu.addAction('Remove Widget')
        chosen = menu.exec(event.globalPos())
        if chosen is lock_action:
            self.lock_toggled.emit(lock_action.isChecked())
        elif chosen is open_action:
            self.open_requested.emit()
        elif chosen is edit_action:
            self.edit_requested.emit()
        elif chosen is remove_action:
            self.remove_requested.emit(self.kind)

    def extra_menu(self, menu: QMenu) -> None:
        pass


# ── Performance ──────────────────────────────────────────────────────────────

class PerformanceWidget(DesktopWidget):
    kind = 'performance'
    size_class = 'medium'

    def __init__(self, rows=('cpu', 'ram', 'gpu', 'ssd'), show_net: bool = True):
        super().__init__()
        self.rows_shown = list(rows)
        self.show_net = show_net
        self._cpu = self._ram = self._gpu = None
        self._ram_text = ''
        self._disk = self._disk_percent()
        self._up = self._down = 0.0
        logo = QPixmap(_LOGO)
        self._logo = logo.scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation) \
            if not logo.isNull() else None
        t = QTimer(self)
        t.setInterval(30_000)
        t.timeout.connect(lambda: (setattr(self, '_disk', self._disk_percent()), self.update()))
        t.start()

    @staticmethod
    def _disk_percent():
        try:
            import psutil
            for path in ('/System/Volumes/Data', '/'):
                if os.path.isdir(path):
                    return float(psutil.disk_usage(path).percent)
        except Exception:
            pass
        return None

    def configure(self, rows, show_net: bool) -> None:
        self.rows_shown = [r for r in ('cpu', 'ram', 'gpu', 'ssd') if r in rows]
        self.show_net = bool(show_net)
        self.update()

    def set_perf(self, data: dict) -> None:
        self._cpu = float(data['cpu'])
        used, total, pct = data['ram']
        self._ram = float(pct)
        self._ram_text = f'{used:.1f} / {total:.0f} GB'
        self._up, self._down = data['net']
        self.update()

    def set_gpu(self, value) -> None:
        self._gpu = None if value is None or value < 0 else float(value)
        self.update()

    def paint_content(self, p, card, dark, c) -> None:
        pad = 14
        x, right = card.left() + pad, card.right() - pad
        y = card.top() + 10
        tx = x
        if self._logo is not None:
            p.drawPixmap(int(x), int(y + 4), self._logo)
            tx = x + 24
        p.setFont(self._f(12.5, QFont.Bold))
        p.setPen(c['text'])
        p.drawText(QRectF(tx, y, right - tx, 26), Qt.AlignVCenter | Qt.AlignLeft, 'Performance')
        p.setFont(self._f(10))
        p.setPen(c['sub'])
        p.drawText(QRectF(tx, y, right - tx, 26), Qt.AlignVCenter | Qt.AlignRight, self._ram_text)
        y += 30
        values = {'cpu': ('CPU', self._cpu), 'ram': ('RAM', self._ram),
                  'gpu': ('GPU', self._gpu), 'ssd': ('SSD', self._disk)}
        rows = [values[r] for r in self.rows_shown if r != 'gpu' or self._gpu is not None]
        bottom = card.bottom() - (26 if self.show_net else 10)
        row_h = min(24.0, (bottom - y) / max(1, len(rows)))
        bar_x, bar_right = x + 38, right - 42
        for name, value in rows:
            p.setFont(self._f(10.5, QFont.Bold))
            p.setPen(c['sub'])
            p.drawText(QRectF(x, y, 38, row_h), Qt.AlignVCenter | Qt.AlignLeft, name)
            bar = QRectF(bar_x, y + (row_h - 7) / 2, bar_right - bar_x, 7)
            p.setPen(Qt.NoPen)
            p.setBrush(c['track'])
            p.drawRoundedRect(bar, 3.5, 3.5)
            if value:
                fill = QRectF(bar.x(), bar.y(), max(7.0, bar.width() * min(value, 100.0) / 100.0), 7)
                grad = QLinearGradient(fill.topLeft(), fill.bottomLeft())
                col = QColor(_ACCENT[name])
                grad.setColorAt(0, col.lighter(100 + round(40 * self._gloss)))
                grad.setColorAt(0.5, col)
                grad.setColorAt(1, col.darker(100 + round(8 * self._gloss)))
                p.setBrush(grad)
                p.drawRoundedRect(fill, 3.5, 3.5)
            p.setFont(self._f(11))
            p.setPen(c['text'])
            p.drawText(QRectF(bar_right, y, right - bar_right, row_h), Qt.AlignVCenter | Qt.AlignRight,
                       '—' if value is None else f'{value:.0f}%')
            y += row_h
        if self.show_net:
            p.setFont(self._f(10))
            p.setPen(c['sub'])
            p.drawText(QRectF(x, card.bottom() - 26, right - x, 20), Qt.AlignVCenter | Qt.AlignLeft,
                       f'↑ {_fmt_rate(self._up)}     ↓ {_fmt_rate(self._down)}')


# ── Clock ────────────────────────────────────────────────────────────────────

class ClockWidget(DesktopWidget):
    kind = 'clock'
    size_class = 'small'

    def __init__(self, style: str = 'digital', seconds: bool = False, h24: bool = True):
        super().__init__()
        self.clock_style, self.seconds, self.h24 = style, seconds, h24
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._arm()

    def configure(self, style: str, seconds: bool, h24: bool) -> None:
        self.clock_style, self.seconds, self.h24 = style, seconds, h24
        self._arm()
        self.update()

    def _arm(self) -> None:
        self._timer.start(1000 if (self.seconds or self.clock_style == 'analog') else 5000)

    def paint_content(self, p, card, dark, c) -> None:
        now = datetime.now()
        if self.clock_style == 'analog':
            self._paint_analog(p, card, dark, c, now)
            return
        fmt = ('%H:%M' if self.h24 else '%I:%M').lstrip()
        text = now.strftime(fmt)
        if not self.h24:
            text = text.lstrip('0')
        p.setPen(c['sub'])
        p.setFont(self._f(11, QFont.DemiBold))
        p.drawText(QRectF(card.left() + 16, card.top() + 14, card.width() - 32, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, now.strftime('%A').upper())
        p.setPen(c['text'])
        p.setFont(self._f(44, QFont.Bold))
        p.drawText(QRectF(card.left() + 12, card.top() + 38, card.width() - 24, 64),
                   Qt.AlignLeft | Qt.AlignVCenter, text)
        p.setFont(self._f(12))
        p.setPen(c['accent'])
        sub = now.strftime('%d %B')
        if self.seconds:
            sub += f'  ·  {now:%S}s'
        if not self.h24:
            sub += f'  ·  {now:%p}'
        p.drawText(QRectF(card.left() + 16, card.bottom() - 40, card.width() - 32, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, sub.lstrip('0'))

    def _paint_analog(self, p, card, dark, c, now) -> None:
        center = card.center()
        r = min(card.width(), card.height()) / 2 - 14
        face = QColor(255, 255, 255, 18) if dark else QColor(255, 255, 255, 150)
        p.setPen(Qt.NoPen)
        p.setBrush(face)
        p.drawEllipse(center, r, r)
        for i in range(60):
            a = math.radians(i * 6)
            outer = QPointF(center.x() + math.sin(a) * (r - 4), center.y() - math.cos(a) * (r - 4))
            inner_r = r - (12 if i % 5 == 0 else 7)
            inner = QPointF(center.x() + math.sin(a) * inner_r, center.y() - math.cos(a) * inner_r)
            p.setPen(QPen(c['text'] if i % 5 == 0 else c['sub'], 2.2 if i % 5 == 0 else 1.0,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(inner, outer)

        def hand(angle_deg, length, width, color):
            a = math.radians(angle_deg)
            end = QPointF(center.x() + math.sin(a) * length, center.y() - math.cos(a) * length)
            tail = QPointF(center.x() - math.sin(a) * 8, center.y() + math.cos(a) * 8)
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(tail, end)
        hand((now.hour % 12 + now.minute / 60) * 30, r * 0.52, 4.5, c['text'])
        hand((now.minute + now.second / 60) * 6, r * 0.78, 3.2, c['text'])
        if self.seconds:
            hand(now.second * 6, r * 0.86, 1.4, QColor('#FF9F0A'))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#FF9F0A') if self.seconds else c['text'])
        p.drawEllipse(center, 4, 4)


# ── Network ──────────────────────────────────────────────────────────────────

class NetworkWidget(DesktopWidget):
    kind = 'network'
    size_class = 'medium'

    def __init__(self):
        super().__init__()
        self._hist = deque([(0.0, 0.0)] * 60, maxlen=60)
        self._up = self._down = 0.0
        self.local_ip = ''
        self.public_ip = ''
        self.last_test: Optional[dict] = None

    def set_perf(self, data: dict) -> None:
        self._up, self._down = data['net']
        self._hist.append((self._up, self._down))
        self.update()

    def set_ips(self, local: str, public: str) -> None:
        self.local_ip, self.public_ip = local or '', public or ''
        self.update()

    def set_speed_test(self, result: Optional[dict]) -> None:
        self.last_test = result
        self.update()

    def paint_content(self, p, card, dark, c) -> None:
        pad = 14
        x, right = card.left() + pad, card.right() - pad
        top = card.top() + 10
        p.setFont(self._f(12.5, QFont.Bold))
        p.setPen(c['text'])
        p.drawText(QRectF(x, top, 120, 24), Qt.AlignVCenter | Qt.AlignLeft, 'Network')
        # current rates, left column
        down_col, up_col = QColor('#0A84FF'), QColor('#FF375F')
        y = top + 30
        for arrow, value, col in (('↓', self._down, down_col), ('↑', self._up, up_col)):
            p.setPen(col)
            p.setFont(self._f(12, QFont.Bold))
            p.drawText(QRectF(x, y, 16, 26), Qt.AlignVCenter | Qt.AlignLeft, arrow)
            p.setPen(c['text'])
            p.setFont(self._f(15, QFont.DemiBold))
            p.drawText(QRectF(x + 16, y, 120, 26), Qt.AlignVCenter | Qt.AlignLeft, _fmt_rate(value))
            y += 28
        # graph, right side
        g = QRectF(x + 140, top + 30, right - x - 140, 58)
        p.setPen(Qt.NoPen)
        p.setBrush(c['track'])
        p.drawRoundedRect(g, 8, 8)
        peak = max(16.0, max(max(u, d) for u, d in self._hist))
        p.setFont(self._f(9))                       # the scale, so a small blip doesn't look big
        p.setPen(c['sub'])
        p.drawText(g.adjusted(0, 3, -7, 0), Qt.AlignTop | Qt.AlignRight, _fmt_rate(peak))
        for idx, col in ((0, up_col), (1, down_col)):      # download on top
            path = QPainterPath()
            n = len(self._hist)
            for i, sample in enumerate(self._hist):
                px = g.left() + 4 + (g.width() - 8) * i / (n - 1)
                py = g.bottom() - 4 - (g.height() - 8) * min(1.0, sample[idx] / peak)
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            p.setPen(QPen(col, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        # addresses and the last speed test
        p.setFont(self._f(10))
        p.setPen(c['sub'])
        line1 = '  ·  '.join(t for t in (f'Local {self.local_ip}' if self.local_ip else '',
                                          f'Public {self.public_ip}' if self.public_ip else '') if t)
        p.drawText(QRectF(x, card.bottom() - 46, right - x, 18), Qt.AlignVCenter | Qt.AlignLeft, line1)
        t = self.last_test
        if t:
            ago = int((time.time() - t.get('time', time.time())) // 60)
            when = 'just now' if ago < 1 else f'{ago} min ago' if ago < 60 else \
                f'{ago // 60} h ago' if ago < 1440 else f'{ago // 1440} d ago'
            line2 = (f"Speed test  ↓ {t.get('download', 0):.0f}  ↑ {t.get('upload', 0):.0f} Mbps"
                     f"  ·  {t.get('ping', 0):.0f} ms  ·  {when}")
        else:
            line2 = 'No speed test yet — Device Manager › Speed Test'
        p.drawText(QRectF(x, card.bottom() - 26, right - x, 18), Qt.AlignVCenter | Qt.AlignLeft, line2)


# ── Music & Audio ────────────────────────────────────────────────────────────

class MusicWidget(DesktopWidget):
    kind = 'music'
    size_class = 'medium'
    opens_app = False                         # clicks are for the player, not for opening ST
    control_requested = Signal(str, str)      # action, app
    volume_requested = Signal(int)            # 0–100, or -1 to toggle mute
    _EQ_BARS = 5

    def __init__(self):
        super().__init__()
        self.np: dict = {'state': 'none'}
        self.vol = (0, False)
        self._art: Optional[QPixmap] = None
        self._art_path = None
        self._buttons: dict = {}
        self._vol_rect = QRectF()
        self._mute_rect = QRectF()
        self._eq_rect = QRectF()
        self._bar_rect = QRectF()
        # Presses show their result at once; the player catches up in the background.
        # Answers asked before a press's command went through would undo that, so they wait.
        self._pressed: Optional[str] = None
        self._pending = 0                       # commands sent, not finished yet
        self._action_at = -1e9
        self._settle_at = -1e9
        self._vol_hold_until = 0.0
        self._vol_sent = 0.0
        self._np_at = time.monotonic()          # when np['position'] was true
        self._eq = [0.0] * self._EQ_BARS        # bar heights, 0–1
        self._anim = QTimer(self)
        self._anim.setInterval(70)
        self._anim.timeout.connect(self._tick)

    def set_now_playing(self, np: dict, vol: tuple, asked_at: Optional[float] = None) -> None:
        if asked_at is not None:
            stale_before = self._settle_at if self._pending == 0 else self._action_at + 3.0
            if asked_at < max(stale_before, self._settle_at):
                return                          # older than the last press
        self.np = np if isinstance(np, dict) else {'state': 'none'}
        self._np_at = time.monotonic()
        if isinstance(vol, tuple) and time.monotonic() >= self._vol_hold_until:
            self.vol = vol
        path = self.np.get('artwork')
        if path != self._art_path:
            self._art_path = path
            img = QImage(path) if path else QImage()
            self._art = QPixmap.fromImage(img) if not img.isNull() else None
        self._sync_anim()
        self.update()

    def settled(self) -> None:
        """A command from a press has gone through (called by the owner)."""
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self._settle_at = time.monotonic()

    def _playing(self) -> bool:
        return self.np.get('state') == 'playing'

    def _position(self) -> float:
        pos = float(self.np.get('position') or 0)
        if self._playing():
            pos += time.monotonic() - self._np_at
        dur = float(self.np.get('duration') or 0)
        return min(pos, dur) if dur > 0 else pos

    # equalizer + live progress ----------------------------------------------

    def _sync_anim(self) -> None:
        moving = self._playing() or any(h > 0.03 for h in self._eq)
        if moving and not self._anim.isActive():
            self._anim.start()
        elif not moving and self._anim.isActive():
            self._anim.stop()

    def _tick(self) -> None:
        level, muted = self.vol
        loud = 0.0 if muted or not self._playing() else 0.3 + 0.7 * max(0, min(100, level)) / 100
        t = time.monotonic()
        for i in range(self._EQ_BARS):
            wave = (math.sin(t * (6.1 + 1.9 * i) + 1.7 * i) * 0.5 + 0.5) * \
                   (math.sin(t * (2.3 + 0.8 * i) + 0.6 * i) * 0.35 + 0.65)
            target = loud * (0.18 + 0.82 * wave)
            self._eq[i] += (target - self._eq[i]) * 0.5
        self._sync_anim()
        self.update(self._eq_rect.adjusted(-2, -2, 2, 2).toAlignedRect())
        self.update(self._bar_rect.adjusted(-2, -2, 2, 2).toAlignedRect())

    def hideEvent(self, event) -> None:
        self._anim.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_anim()

    def paint_content(self, p, card, dark, c) -> None:
        pad = 14
        art = QRectF(card.left() + pad, card.top() + pad, 78, 78)
        clip = QPainterPath()
        clip.addRoundedRect(art, 10, 10)
        if self._art is not None:
            p.save()
            p.setClipPath(clip, Qt.IntersectClip)
            p.drawPixmap(art.toRect(), self._art.scaled(art.size().toSize() * 2, Qt.KeepAspectRatioByExpanding,
                                                        Qt.SmoothTransformation))
            p.restore()
        else:
            grad = QLinearGradient(art.topLeft(), art.bottomRight())
            grad.setColorAt(0, QColor('#FF375F'))
            grad.setColorAt(1, QColor('#BF5AF2'))
            p.fillPath(clip, grad)
            p.setPen(QColor(255, 255, 255, 230))
            p.setFont(self._f(30, QFont.Bold))
            p.drawText(art, Qt.AlignCenter, '♫')
        np = self.np
        state = np.get('state')
        tx = art.right() + 12
        tw = card.right() - pad - tx
        if state in ('playing', 'paused') and np.get('title'):
            title, artist = np.get('title', ''), np.get('artist', '')
            source = np.get('label', '') + ('' if state == 'playing' else '  ·  Paused')
        elif state == 'denied':
            title, artist = 'Allow control', f"System Settings › Privacy › Automation › {np.get('label')}"
            source = 'macOS asked for permission'
        else:
            title, artist = 'Not Playing', 'Apple Music · Spotify · YouTube · SoundCloud …'
            source = 'Music & Audio'
        fm_t = self._f(12.5, QFont.Bold)
        p.setFont(self._f(9.5, QFont.DemiBold))
        p.setPen(c['accent'])
        eq_w = 24
        p.drawText(QRectF(tx, card.top() + pad, tw - eq_w - 6, 16), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(source.upper(), Qt.ElideRight, int(tw - eq_w - 6)))
        # equalizer: moves with the music, as loud as the volume; flat when paused or muted
        self._eq_rect = QRectF(card.right() - pad - eq_w, card.top() + pad - 1, eq_w, 17)
        bar_w, gap = 3.0, (eq_w - 3.0 * self._EQ_BARS) / (self._EQ_BARS - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(c['accent'])
        for i, h in enumerate(self._eq):
            bh = max(2.5, h * self._eq_rect.height())
            p.drawRoundedRect(QRectF(self._eq_rect.left() + i * (bar_w + gap),
                                     self._eq_rect.bottom() - bh, bar_w, bh), 1.3, 1.3)
        p.setFont(fm_t)
        p.setPen(c['text'])
        p.drawText(QRectF(tx, card.top() + pad + 18, tw, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(tw)))
        p.setFont(self._f(11))
        p.setPen(c['sub'])
        p.drawText(QRectF(tx, card.top() + pad + 38, tw, 18), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(artist, Qt.ElideRight, int(tw)))
        # progress
        dur, pos = np.get('duration') or 0, self._position()
        bar = QRectF(tx, card.top() + pad + 64, tw, 4)
        self._bar_rect = bar
        p.setPen(Qt.NoPen)
        p.setBrush(c['track'])
        p.drawRoundedRect(bar, 2, 2)
        if dur > 0:
            p.setBrush(c['text'])
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(1.0, pos / dur), 4), 2, 2)
        # controls
        y = card.bottom() - 40
        cx = card.left() + pad + 39
        self._buttons = {}
        for name, dx in (('previous', -30), ('playpause', 0), ('next', 30)):
            r = QRectF(cx + dx - 13, y, 26, 26)
            self._buttons[name] = r
            if self._pressed == name:            # pressed: a soft disc behind, icon a bit smaller
                p.setPen(Qt.NoPen)
                p.setBrush(c['track'])
                p.drawEllipse(r.center(), 16, 16)
                p.save()
                p.translate(r.center())
                p.scale(0.88, 0.88)
                p.translate(-r.center())
            self._draw_icon(p, name, r, c['text'], playing=state == 'playing')
            if self._pressed == name:
                p.restore()
        # volume
        level, muted = self.vol
        self._mute_rect = QRectF(tx, y + 2, 22, 22)
        p.setPen(c['text'])
        p.setFont(self._f(13))
        p.drawText(self._mute_rect, Qt.AlignCenter, '🔇' if muted or level == 0 else '🔊')
        self._vol_rect = QRectF(tx + 28, y + 11, card.right() - pad - tx - 28, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(c['track'])
        p.drawRoundedRect(self._vol_rect, 2.5, 2.5)
        if not muted and level:
            p.setBrush(c['accent'])
            p.drawRoundedRect(QRectF(self._vol_rect.x(), self._vol_rect.y(),
                                     self._vol_rect.width() * level / 100, 5), 2.5, 2.5)

    @staticmethod
    def _draw_icon(p, name, r, color, playing) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        cx, cy = r.center().x(), r.center().y()

        def tri(x0, flip=False):
            path = QPainterPath()
            if not flip:
                path.moveTo(x0, cy - 7); path.lineTo(x0 + 10, cy); path.lineTo(x0, cy + 7)
            else:
                path.moveTo(x0 + 10, cy - 7); path.lineTo(x0, cy); path.lineTo(x0 + 10, cy + 7)
            path.closeSubpath()
            p.drawPath(path)
        if name == 'playpause':
            if playing:
                p.drawRoundedRect(QRectF(cx - 6, cy - 8, 4.5, 16), 1.5, 1.5)
                p.drawRoundedRect(QRectF(cx + 1.5, cy - 8, 4.5, 16), 1.5, 1.5)
            else:
                tri(cx - 4)
        elif name == 'next':
            tri(cx - 9); tri(cx - 1)
        else:
            tri(cx - 9, flip=True); tri(cx - 1, flip=True)

    # mouse: controls act the moment they're pressed --------------------------

    def _hit(self, pos: QPointF) -> Optional[str]:
        grow = 6
        for name, r in self._buttons.items():
            if r.adjusted(-grow, -grow, grow, grow).contains(pos):
                return name
        if self._mute_rect.adjusted(-4, -4, 4, 4).contains(pos):
            return 'mute'
        if self._vol_rect.adjusted(-2, -10, 2, 10).contains(pos):
            return 'volume'
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            hit = self._hit(event.position())
            if hit:
                self._pressed = hit
                self._act(hit, event.position())
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pressed == 'volume':
            self._set_volume_at(event.position(), final=False)
        elif self._pressed is None:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pressed is not None and event.button() == Qt.LeftButton:
            if self._pressed == 'volume':
                self._set_volume_at(event.position(), final=True)
            self._pressed = None
            self.update()
            return
        super().mouseReleaseEvent(event)

    def _act(self, name: str, pos: QPointF) -> None:
        now = time.monotonic()
        if name == 'volume':
            self._set_volume_at(pos, final=False, force=True)
            return
        self._action_at = now
        self._pending += 1
        if name == 'mute':
            level, muted = self.vol
            self.vol = (level, not muted)
            self._vol_hold_until = now + 1.5
            self.volume_requested.emit(-1)
            return
        np = dict(self.np)
        if np.get('state') in ('playing', 'paused'):
            np['position'] = self._position()
            if name == 'playpause':
                np['state'] = 'paused' if np['state'] == 'playing' else 'playing'
            else:                                   # next / previous start a song from the top
                np['state'], np['position'] = 'playing', 0.0
            self.np = np
            self._np_at = now
        self._sync_anim()
        self.control_requested.emit(name, np.get('app') or '')

    def _set_volume_at(self, pos: QPointF, final: bool, force: bool = False) -> None:
        width = max(1.0, self._vol_rect.width())
        level = max(0, min(100, round(100 * (pos.x() - self._vol_rect.left()) / width)))
        now = time.monotonic()
        self.vol = (level, False)
        self._vol_hold_until = now + 1.5
        self.update()
        if final or force or now - self._vol_sent > 0.1:
            self._vol_sent = now
            self.volume_requested.emit(level)


WIDGET_CLASSES = {'performance': PerformanceWidget, 'clock': ClockWidget,
                  'network': NetworkWidget, 'music': MusicWidget}
