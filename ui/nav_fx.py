"""Section changes that move (Mac and Windows), after React Bits' PillNav with
hoverEffect="glide", indicator="pill", bounce=0.47:

  * a pill glides under the tab row to the section the mouse is over and rests on the current
    one; its leading edge runs ahead and the trailing edge catches up (it stretches, squashes
    a little and overshoots as it settles), and it gives slightly while pressed
  * a small bar under the current section springs to the new one, stretching with its speed
  * the new section's page slides in from the side you moved toward and fades in

install_all(widget) equips every tab row (QTabWidget) inside `widget`; set_enabled(False)
turns it off. With the system's "reduce motion" on, things jump into place instead.
"""
from __future__ import annotations

import math
import time
import weakref
from typing import Optional

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QTabWidget, QWidget

from ui import scroll_fx

ENABLED = True
BOUNCE = 0.47
STRETCH = 0.5
PAGE_MS = 300
PAGE_SHIFT = 26

_ALL = weakref.WeakSet()
_hooked_quit = False


def set_enabled(on: bool) -> None:
    global ENABLED
    ENABLED = bool(on)
    for fx in list(_ALL):
        fx.finish_page()
        fx.glide.update()


def _shutdown() -> None:
    for fx in list(_ALL):
        fx.detach()


def install_all(root: QWidget) -> None:
    global _hooked_quit
    app = QApplication.instance()
    if app is not None and not _hooked_quit:
        app.aboutToQuit.connect(_shutdown)
        _hooked_quit = True
    for tabs in ([root] if isinstance(root, QTabWidget) else []) + root.findChildren(QTabWidget):
        if getattr(tabs, '_st_nav', None) is None:
            tabs._st_nav = _TabFx(tabs)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class _Glide(QWidget):
    """Painted over the tab row; never takes the mouse."""

    def __init__(self, fx: '_TabFx'):
        super().__init__(fx.bar)
        self.fx = fx
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, _event) -> None:
        if ENABLED:
            self.fx.paint(self)


class _TabFx(QObject):
    def __init__(self, tabs: QTabWidget):
        super().__init__(tabs)
        self.tabs = tabs
        self.bar = tabs.tabBar()
        self.bar.setMouseTracking(True)
        self.bar.installEventFilter(self)
        self.glide = _Glide(self)
        self.glide.setGeometry(self.bar.rect())
        self.glide.show()                     # children added to a shown parent start hidden
        self.glide.raise_()
        self.s = dict(x0=0.0, x1=0.0, v0=0.0, v1=0.0, alpha=0.0, pop=1.0, press=1.0, pv=0.0,
                      dot=0.0, dv=0.0, dalpha=0.0, hover=-1, pressing=False)
        self.prev = tabs.currentIndex()
        self.page = None            # (widget, effect, start, dx, home)
        self._detached = False
        self._last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        tabs.currentChanged.connect(self._changed)
        _ALL.add(self)
        self._start()

    def alive(self) -> bool:
        return not self._detached and shiboken6.isValid(self.tabs) and shiboken6.isValid(self.bar)

    def detach(self) -> None:
        self._detached = True
        self.timer.stop()
        try:
            self.bar.removeEventFilter(self)
        except RuntimeError:
            pass

    # events -------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.MouseMove:
            hover = self.bar.tabAt(event.position().toPoint())
            if hover != self.s['hover']:
                self.s['hover'] = hover
                self._start()
        elif kind in (QEvent.Leave, QEvent.Hide):
            self.s['hover'] = -1
            self.s['pressing'] = False
            self._start()
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.s['pressing'] = True
            self._start()
        elif kind == QEvent.MouseButtonRelease:
            self.s['pressing'] = False
            self._start()
        elif kind in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show):
            self.glide.setGeometry(self.bar.rect())
            self.glide.raise_()
            self._start()
        return False

    # page change --------------------------------------------------------------

    def _changed(self, index: int) -> None:
        if not self.alive():
            return
        direction = 1 if index > self.prev else -1
        self.prev = index
        self.finish_page()
        self._start()
        page = self.tabs.widget(index)
        if not ENABLED or scroll_fx.REDUCED or page is None or page.graphicsEffect() is not None:
            return
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        home = page.pos()
        page.move(home + QPoint(direction * PAGE_SHIFT, 0))
        self.page = (page, effect, time.monotonic(), direction * PAGE_SHIFT, home)

    def finish_page(self) -> None:
        if self.page is None:
            return
        page, _effect, _start, _dx, home = self.page
        self.page = None
        if shiboken6.isValid(page):
            page.move(home)
            page.setGraphicsEffect(None)

    def _step_page(self) -> bool:
        page, effect, start, dx, home = self.page
        if not shiboken6.isValid(page):
            self.page = None
            return False
        t = (time.monotonic() - start) * 1000 / PAGE_MS
        if t >= 1:
            self.finish_page()
            return False
        e = 1 - (1 - t) ** 4                             # ease out
        page.move(home + QPoint(round(dx * (1 - e)), 0))
        effect.setOpacity(min(1.0, t * 1.6))
        return True

    # the pill and the dot -------------------------------------------------------

    def _rect(self, index: int) -> Optional[QRectF]:
        if index < 0 or index >= self.bar.count():
            return None
        r = self.bar.tabRect(index)
        return QRectF(r) if not r.isEmpty() else None

    def _glide_index(self) -> int:
        return self.s['hover'] if self.s['hover'] >= 0 else self.bar.currentIndex()

    def _step(self, dt: float) -> bool:
        s = self.s
        reduce = scroll_fx.REDUCED
        busy = False
        zeta = 1 - 0.55 * BOUNCE
        target = self._rect(self._glide_index())
        if target is not None:
            t0, t1 = target.left(), target.right()
            if s['alpha'] < 0.02:
                s.update(x0=t0, x1=t1, v0=0.0, v1=0.0, pop=0.9)
            if reduce:
                s.update(x0=t0, x1=t1, v0=0.0, v1=0.0)
            else:
                base = 520.0
                direction = math.copysign(1, (t0 + t1) / 2 - (s['x0'] + s['x1']) / 2) \
                    if abs((t0 + t1) / 2 - (s['x0'] + s['x1']) / 2) > 0.01 else 0
                lead, trail = base * (1 + 0.9 * STRETCH), base * (1 - 0.5 * STRETCH)
                k0 = lead if direction < 0 else trail if direction > 0 else base
                k1 = lead if direction > 0 else trail if direction < 0 else base
                steps = max(1, math.ceil(dt / (1 / 240)))
                h = dt / steps
                for _ in range(steps):
                    s['v0'] += (-k0 * (s['x0'] - t0) - 2 * zeta * math.sqrt(k0) * s['v0']) * h
                    s['x0'] += s['v0'] * h
                    s['v1'] += (-k1 * (s['x1'] - t1) - 2 * zeta * math.sqrt(k1) * s['v1']) * h
                    s['x1'] += s['v1'] * h
            s['alpha'] += (1 - s['alpha']) * (1 if reduce else 1 - math.exp(-dt / 0.06))
            busy |= (abs(s['x0'] - t0) > 0.05 or abs(s['x1'] - t1) > 0.05 or abs(s['v0']) > 0.5
                     or abs(s['v1']) > 0.5 or s['alpha'] < 0.999)
        else:
            s['alpha'] += (0 - s['alpha']) * (1 if reduce else 1 - math.exp(-dt / 0.07))
            busy |= s['alpha'] > 0.002
        s['pop'] += (1 - s['pop']) * (1 if reduce else 1 - math.exp(-dt / 0.09))
        busy |= abs(1 - s['pop']) > 0.001
        press_to = 0.95 if s['pressing'] else 1.0
        if reduce:
            s['press'], s['pv'] = press_to, 0.0
        else:
            s['pv'] += (-700 * (s['press'] - press_to) - 30 * s['pv']) * dt
            s['press'] += s['pv'] * dt
        busy |= abs(s['press'] - press_to) > 0.0005 or abs(s['pv']) > 0.01

        active = self._rect(self.bar.currentIndex())
        if active is not None:
            center = active.center().x()
            if s['dalpha'] < 0.02:
                s['dot'], s['dv'] = center, 0.0
            if reduce:
                s['dot'], s['dv'] = center, 0.0
            else:
                k = 360.0
                steps = max(1, math.ceil(dt / (1 / 240)))
                h = dt / steps
                for _ in range(steps):
                    s['dv'] += (-k * (s['dot'] - center) - 2 * (1 - 0.5 * BOUNCE) * math.sqrt(k) * s['dv']) * h
                    s['dot'] += s['dv'] * h
            s['dalpha'] += (1 - s['dalpha']) * (1 if reduce else 1 - math.exp(-dt / 0.1))
            busy |= abs(s['dot'] - center) > 0.05 or abs(s['dv']) > 0.5 or s['dalpha'] < 0.999
        return busy

    def paint(self, widget: QWidget) -> None:
        s = self.s
        accent = widget.palette().highlight().color()
        p = QPainter(widget)
        p.setRenderHint(QPainter.Antialiasing)
        target = self._rect(self._glide_index())
        if s['alpha'] > 0.01 and target is not None:
            width = max(0.0, s['x1'] - s['x0'])
            scale = s['press'] * s['pop']
            squash = 1 - _clamp((width / max(target.width(), 1) - 1) * 0.3, 0, 0.14)
            cx = (s['x0'] + s['x1']) / 2
            cy = target.center().y()
            w = width * scale - 6
            h = (target.height() - 8) * scale * squash
            rect = QRectF(cx - w / 2, cy - h / 2, w, h)
            fill = QColor(accent)
            fill.setAlphaF(0.20 * s['alpha'])
            line = QColor(accent).lighter(140)
            line.setAlphaF(0.55 * s['alpha'])
            p.setBrush(fill)
            p.setPen(QPen(line, 1.2))
            p.drawRoundedRect(rect, h / 2, h / 2)
        active = self._rect(self.bar.currentIndex())
        if s['dalpha'] > 0.01 and active is not None:
            pull = _clamp(abs(s['dv']) / 700, 0, 2.2)
            w = 16 * (1 + pull)
            dot = QColor(accent)
            dot.setAlphaF(s['dalpha'])
            p.setPen(Qt.NoPen)
            p.setBrush(dot)
            p.drawRoundedRect(QRectF(s["dot"] - w / 2, active.bottom() - 2.5, w, 3), 1.5, 1.5)

    # driver -------------------------------------------------------------------

    def _start(self) -> None:
        if not self.timer.isActive():
            self._last = time.monotonic()
            self.timer.start()

    def _tick(self) -> None:
        if not self.alive():
            self.timer.stop()
            return
        now = time.monotonic()
        dt = min(0.05, now - self._last) or 1 / 60
        self._last = now
        busy = self._step(dt)
        if self.page is not None:
            busy |= self._step_page()
        self.glide.update()
        if not busy:
            self.timer.stop()
