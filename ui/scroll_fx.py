"""Scrolling effects for every list, table and tree in the app (Mac and Windows), after
React Bits' AnimatedList:

  * rows pop in with a spring as they scroll into view (from above or below), and when a list
    first appears or is filled again they come in one after another
  * rows fade out at the top / bottom edge while there is more to scroll
  * a ring glides with a spring to the selected row
  * a mouse wheel scrolls smoothly instead of jumping (trackpads are already smooth: untouched)

install_all(widget) adds the effects to every scroll area inside `widget` (run again after new
views appear — already-equipped ones are skipped). set_enabled(False) turns all of it off.
With the system's "reduce motion" on, rows only fade and the ring jumps.
"""
from __future__ import annotations

import subprocess
import sys
import time
import weakref
from typing import Optional

import shiboken6
from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QListView,
                               QStyledItemDelegate, QWidget)

ENABLED = True

POP_MS = 620            # row position / scale spring
FADE_MS = 300           # row opacity
STAGGER = 0.045         # s between rows when a list appears
STAGGER_MAX = 0.40
EDGE_PX = 44            # height of the edge fades
SHIFT_PX = 12           # how far a row travels in
WHEEL_MS = 230          # smooth wheel scroll

# The component's spring (CSS linear() easing): a little overshoot, then settles
_SPRING = (0, 0.0603, 0.2033, 0.3821, 0.5633, 0.7258, 0.8584, 0.9574, 1.0242, 1.0634, 1.0809,
           1.0828, 1.0747, 1.0613, 1.046, 1.0313, 1.0186, 1.0085, 1.0012, 0.9965, 0.994, 0.9931,
           0.9933, 0.9941, 0.9953, 0.9966, 0.9978, 0.9988, 1)


def _spring(t: float) -> float:
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    x = t * (len(_SPRING) - 1)
    i = int(x)
    return _SPRING[i] + (_SPRING[i + 1] - _SPRING[i]) * (x - i)


def _ease_out(t: float) -> float:          # ≈ cubic-bezier(0.22, 1, 0.36, 1)
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 4


def _smootherstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def _system_reduces_motion() -> bool:
    try:
        if sys.platform == 'darwin':
            r = subprocess.run(['/usr/bin/defaults', 'read', 'com.apple.universalaccess', 'reduceMotion'],
                               capture_output=True, text=True, timeout=3)
            return r.stdout.strip() == '1'
        if sys.platform == 'win32':
            import ctypes
            on = ctypes.c_bool(True)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # client-area animations
            return not on.value
    except Exception:
        pass
    return False


REDUCED = _system_reduces_motion()
_ALL = weakref.WeakSet()


def set_enabled(on: bool) -> None:
    global ENABLED
    ENABLED = bool(on)
    for fx in list(_ALL):
        fx.reset(replay=False)


def _shutdown() -> None:
    """Before the app quits: take the event filters off, so Qt deleting the views can't
    call back into Python while it is shutting down."""
    for fx in list(_ALL):
        fx.detach()


_hooked_quit = False


def install_all(root: QWidget, skip=()) -> None:
    """Equip every scroll area under `root` (lists, tables, trees, text boxes, pages)."""
    global _hooked_quit
    app = QApplication.instance()
    if app is not None and not _hooked_quit:
        app.aboutToQuit.connect(_shutdown)
        _hooked_quit = True
    for area in ([root] if isinstance(root, QAbstractScrollArea) else []) + root.findChildren(QAbstractScrollArea):
        if getattr(area, '_st_fx', None) is None and not any(_inside(area, s) for s in skip):
            area._st_fx = _Fx(area)


def _inside(widget: QWidget, ancestor: Optional[QWidget]) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.parentWidget()
    return False


# ── rows ─────────────────────────────────────────────────────────────────────

class _PopDelegate(QStyledItemDelegate):
    """The standard row painting, moved / scaled / faded by the row's animation."""

    def __init__(self, fx: '_Fx'):
        super().__init__(fx.area)
        self.fx = fx

    def paint(self, painter: QPainter, option, index) -> None:
        fx = self.fx
        if not ENABLED:
            return super().paint(painter, option, index)
        dy, scale, opacity = fx.row_pose(option.rect, index)
        if opacity <= 0.003:
            return
        if dy == 0 and scale == 1 and opacity >= 0.999:
            return super().paint(painter, option, index)
        painter.save()
        painter.setOpacity(painter.opacity() * opacity)
        if dy or scale != 1:
            cx = fx.area.viewport().width() / 2
            cy = option.rect.center().y()
            painter.translate(cx, cy + dy)
            painter.scale(scale, scale)
            painter.translate(-cx, -cy)
        super().paint(painter, option, index)
        painter.restore()


class _Ring(QWidget):
    """The selection ring, over the list's viewport; never takes the mouse."""

    def __init__(self, fx: '_Fx'):
        super().__init__(fx.area)
        self.fx = fx
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, _event) -> None:
        fx = self.fx
        m = fx.ring
        if not ENABLED or not m['shown'] or m['h'] <= 1:
            return
        bar = fx.area.verticalScrollBar()
        rect = QRectF(m['x'], m['y'] - bar.value(), m['w'], m['h']).adjusted(1, 1, -1, -1)
        accent = self.palette().highlight().color()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fill = QColor(accent)
        fill.setAlpha(26)
        line = QColor(accent).lighter(135)
        line.setAlpha(210)
        p.setBrush(fill)
        p.setPen(QPen(line, 1.6))
        radius = min(8.0, rect.height() / 2)
        p.drawRoundedRect(rect, radius, radius)


# ── one scroll area ──────────────────────────────────────────────────────────

class _Fx(QObject):
    def __init__(self, area: QAbstractScrollArea):
        super().__init__(area)
        self.area = area
        self.view: Optional[QAbstractItemView] = area if isinstance(area, QAbstractItemView) else None
        self.rows: dict = {}          # key → (start, direction)   rows animating in
        self.shown: dict = {}         # key → (top, bottom)         rows in view (content coords)
        self.entrance_until = 0.0
        self.entrance_order = 0
        self.ring = {'x': 0.0, 'y': 0.0, 'w': 0.0, 'h': 0.0, 'vx': 0.0, 'vy': 0.0, 'vw': 0.0,
                     'vh': 0.0, 'tx': 0.0, 'ty': 0.0, 'tw': 0.0, 'th': 0.0, 'shown': False, 'ready': False}
        self.wheel = None             # (start, from, to)
        self._detached = False
        self._model = None
        self._last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        _ALL.add(self)

        area.viewport().installEventFilter(self)
        area.installEventFilter(self)
        area.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.ring_widget = None
        if self.view is not None:
            view = self.view
            view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            if type(view.itemDelegate()) is QStyledItemDelegate:
                self.delegate = _PopDelegate(self)
                view.setItemDelegate(self.delegate)
            self.ring_widget = _Ring(self)
            self.ring_widget.show()           # children added to a shown parent start hidden
            self._sync_ring_geometry()
            self._hook_model()
            self.reset(replay=True)

    def alive(self) -> bool:
        return not self._detached and shiboken6.isValid(self.area)

    def detach(self) -> None:
        self._detached = True
        self.timer.stop()
        try:
            self.area.viewport().removeEventFilter(self)
            self.area.removeEventFilter(self)
        except RuntimeError:                            # already deleted
            pass

    # rows -------------------------------------------------------------------

    @staticmethod
    def key(index) -> tuple:
        parts = []
        while index.isValid():
            parts.append(index.row())
            index = index.parent()
        return tuple(parts)

    def reset(self, replay: bool) -> None:
        """Forget what has been shown; with replay the rows come in one after another."""
        self.rows.clear()
        self.shown.clear()
        self.entrance_order = 0
        self.entrance_until = time.monotonic() + 0.3 if replay else 0.0
        if self.view is not None:
            self.view.viewport().update()
            self._retarget_ring(snap=True)

    def row_pose(self, rect, index) -> tuple:
        """(dy, scale, opacity) for a row being painted now."""
        now = time.monotonic()
        key = self.key(index)
        top = rect.top() + self.area.verticalScrollBar().value()
        if key not in self.shown:                       # just came into view
            delay = 0.0
            if now < self.entrance_until:
                delay = min(STAGGER_MAX, self.entrance_order * STAGGER)
                self.entrance_order += 1
            below = rect.center().y() > self.area.viewport().height() / 2
            self.rows[key] = (now + delay, 1 if below else -1)
            self._start()
        self.shown[key] = (top, top + rect.height())

        dy, scale, opacity = 0.0, 1.0, 1.0
        anim = self.rows.get(key)
        if anim is not None:
            start, direction = anim
            t = (now - start) * 1000
            if t >= POP_MS:
                del self.rows[key]
            else:
                pos = _spring(t / POP_MS)
                opacity = _ease_out(t / FADE_MS)
                if REDUCED:
                    dy = direction * 4 * (1 - _ease_out(t / FADE_MS))
                else:
                    width = max(1, self.area.viewport().width())
                    shrink = min(0.2, 48 / width)           # wide rows shrink less
                    scale = 1 - shrink * (1 - pos)
                    dy = direction * SHIFT_PX * (1 - pos)
        # edge fades while there is more to scroll
        bar = self.area.verticalScrollBar()
        view_h = self.area.viewport().height()
        fade_top = min(EDGE_PX, bar.value() - bar.minimum())
        fade_bottom = min(EDGE_PX, bar.maximum() - bar.value())
        cy = rect.center().y()
        if fade_top > 0 and cy < fade_top:
            opacity *= _smootherstep(cy / fade_top)
        if fade_bottom > 0 and cy > view_h - fade_bottom:
            opacity *= _smootherstep((view_h - cy) / fade_bottom)
        return dy, scale, opacity

    def _scrolled(self, value: int) -> None:
        if not ENABLED or not self.alive():
            return
        view_h = self.area.viewport().height()
        gone = [k for k, (top, bottom) in self.shown.items() if bottom <= value or top >= value + view_h]
        for k in gone:
            del self.shown[k]
            self.rows.pop(k, None)
        if self.view is not None:
            self.view.viewport().update()               # edge fades follow every row
            if self.ring_widget is not None:
                self.ring_widget.update()

    # model ------------------------------------------------------------------

    def _hook_model(self) -> None:
        model = self.view.model() if self.view is not None else None
        if model is None or model is self._model:
            return
        self._model = model
        model.modelReset.connect(self._model_changed)
        model.rowsRemoved.connect(self._model_changed)
        model.layoutChanged.connect(self._ring_snap)
        model.rowsInserted.connect(self._ring_snap)
        sel = self.view.selectionModel()
        if sel is not None:
            sel.selectionChanged.connect(self._ring_glide)
            sel.currentChanged.connect(self._ring_glide)

    def _ring_snap(self, *_args) -> None:
        self._retarget_ring(snap=True)

    def _ring_glide(self, *_args) -> None:
        self._retarget_ring(snap=False)

    def _model_changed(self, *_args) -> None:
        if not self.alive():
            return
        # emptied (e.g. a list being refilled): the new rows come in one after another;
        # a live table refreshing its rows keeps still
        if self._model is not None and self._model.rowCount() == 0:
            self.reset(replay=True)
        else:
            self._retarget_ring(snap=True)

    # selection ring ---------------------------------------------------------

    def _sync_ring_geometry(self) -> None:
        if self.ring_widget is not None:
            self.ring_widget.setGeometry(self.area.viewport().geometry())
            self.ring_widget.raise_()

    def _retarget_ring(self, snap: bool) -> None:
        view, m = self.view, self.ring
        if view is None or self.ring_widget is None or not self.alive():
            return
        sel = view.selectionModel()
        index = view.currentIndex()
        if sel is None or not index.isValid() or not sel.isSelected(index):
            rows = sel.selectedIndexes() if sel is not None else []
            index = rows[0] if rows else None
        if index is None or not index.isValid():
            m['shown'] = False
            self.ring_widget.update()
            return
        r = view.visualRect(index)
        if r.isEmpty():                                 # e.g. scrolled far away: keep last
            return
        bar = view.verticalScrollBar()
        icon_mode = isinstance(view, QListView) and view.viewMode() == QListView.IconMode
        if icon_mode:
            x, w = r.x(), r.width()
        else:
            x, w = 2.0, max(10.0, view.viewport().width() - 4.0)
        m.update(tx=float(x), ty=float(r.y() + bar.value()), tw=float(w), th=float(r.height()),
                 shown=True)
        if snap or REDUCED or not m['ready'] or not ENABLED:
            m.update(x=m['tx'], y=m['ty'], w=m['tw'], h=m['th'], vx=0.0, vy=0.0, vw=0.0, vh=0.0,
                     ready=True)
            self.ring_widget.update()
        else:
            self._start()

    def _step_ring(self, dt: float) -> bool:
        m = self.ring
        omega, zeta = 30.0, 0.78                        # the component's spring
        steps = max(1, int(dt / (1 / 240)) + 1)
        h = dt / steps
        for _ in range(steps):
            for a in ('x', 'y', 'w', 'h'):
                v = 'v' + a
                m[v] += (omega * omega * (m['t' + a] - m[a]) - 2 * zeta * omega * m[v]) * h
                m[a] += m[v] * h
        moving = any(abs(m['t' + a] - m[a]) > 0.05 or abs(m['v' + a]) > 0.5 for a in ('x', 'y', 'w', 'h'))
        if not moving:
            for a in ('x', 'y', 'w', 'h'):
                m[a], m['v' + a] = m['t' + a], 0.0
        return moving

    # smooth wheel -----------------------------------------------------------

    def _wheel(self, event) -> bool:
        if not ENABLED or event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier):
            return False
        if not event.pixelDelta().isNull():             # trackpad / Magic Mouse: already smooth
            return False
        notches = event.angleDelta().y() / 120
        if not notches:
            return False
        bar = self.area.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            return False
        line = max(18, min(bar.singleStep(), 40))
        base = self.wheel[2] if self.wheel else bar.value()
        target = max(bar.minimum(), min(bar.maximum(), round(base - notches * line * QApplication.wheelScrollLines())))
        if target == bar.value() and not self.wheel:
            return False                                # at the end: let the page around it scroll
        self.wheel = (time.monotonic(), bar.value(), target)
        self._start()
        return True

    def _step_wheel(self) -> bool:
        start, frm, to = self.wheel
        t = (time.monotonic() - start) * 1000 / WHEEL_MS
        bar = self.area.verticalScrollBar()
        if t >= 1:
            bar.setValue(to)
            self.wheel = None
            return False
        bar.setValue(round(frm + (to - frm) * (1 - (1 - t) ** 3)))
        return True

    # driver -----------------------------------------------------------------

    def _start(self) -> None:
        if not self.timer.isActive():
            self._last = time.monotonic()
            self.timer.start()

    def _tick(self) -> None:
        if not self.alive():
            self.timer.stop()
            return
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        busy = False
        if self.wheel:
            busy |= self._step_wheel()
        if self.rows:
            busy = True
            self.area.viewport().update()
        if self.ring_widget is not None and self.ring['shown'] and self._step_ring(dt):
            busy = True
            self.ring_widget.update()
        elif self.ring_widget is not None:
            self.ring_widget.update()
        if not busy:
            self.timer.stop()

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if obj is self.area.viewport():
            if kind == QEvent.Wheel:
                return self._wheel(event)
            if kind == QEvent.Resize:
                self._sync_ring_geometry()
                self._retarget_ring(snap=True)
        elif obj is self.area:
            if kind == QEvent.Show and self.view is not None:
                self._hook_model()                      # the model may have been set since
                self.reset(replay=True)                 # a tab opened: rows come in again
            elif kind in (QEvent.Resize, QEvent.LayoutRequest):
                self._sync_ring_geometry()
        return False
