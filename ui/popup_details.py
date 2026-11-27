"""More system details under the menu-bar popup's performance card: cores, memory, disk,
network, battery and the busiest apps. Painted like PerfPanel and following light / dark."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from ui.perf_widget import _W, is_dark

_PAD   = 16
_HEAD  = 24      # section title
_ROW   = 19      # key / value row
_GAP   = 8       # space between sections
_CORE_H = 30     # per-core bar strip

_GREEN, _ORANGE, _RED = QColor('#30D158'), QColor('#FF9F0A'), QColor('#FF453A')
_BLUE = QColor('#0A84FF')


def _gb(n) -> str:
    return '—' if n is None else f'{n / 1024 ** 3:.1f} GB'


def _rate(kb_s: float) -> str:
    return f'{kb_s / 1024:.1f} MB/s' if kb_s >= 1024 else f'{kb_s:.0f} KB/s'


def _duration(secs: Optional[int]) -> str:
    if not secs:
        return ''
    h, m = divmod(int(secs) // 60, 60)
    return f'{h}:{m:02d} left'


class DetailsPanel(QWidget):
    """Feed it with set_snapshot(core.sysinfo.snapshot()) and set_perf(perf sample)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._s: dict = {}
        self._disk_r = self._disk_w = 0.0
        self._fit()

    # ── data in ───────────────────────────────────────────────────────────────

    def set_snapshot(self, snap: dict) -> None:
        if not isinstance(snap, dict):
            return
        had_battery = bool(self._s.get('battery'))
        self._s = snap
        if had_battery != bool(snap.get('battery')) or not self.height():
            self._fit()
        self.update()

    def set_perf(self, data: dict) -> None:
        """Disk read / write speed from the app's once-a-second sample (MB/s)."""
        try:
            self._disk_r, self._disk_w = data['disk']
        except (KeyError, TypeError, ValueError):
            return
        self.update()

    # ── layout ────────────────────────────────────────────────────────────────

    # Settings › Widgets & Menu Bar keys for each section
    _KEYS = {'cpu': 'cores', 'mem': 'memory', 'disk': 'disk', 'net': 'network',
             'battery': 'battery', 'top': 'top'}
    shown = None            # None = everything; else a set of keys (plus 'system')

    def set_shown(self, keys) -> None:
        self.shown = set(keys) if keys is not None else None
        self._fit()
        self.update()

    def _on(self, key: str) -> bool:
        return self.shown is None or key in self.shown

    def _sections(self):
        sections = [('cpu', _HEAD + _CORE_H + 4), ('mem', _HEAD + 2 * _ROW),
                    ('disk', _HEAD + 2 * _ROW), ('net', _HEAD + _ROW)]
        if self._s.get('battery'):
            sections.append(('battery', _HEAD + 2 * _ROW))
        sections.append(('top', _HEAD + 3 * _ROW))
        return [(name, h) for name, h in sections if self._on(self._KEYS[name])]

    def _fit(self) -> None:
        h = sum(height + _GAP for _, height in self._sections()) + (_ROW + 4 if self._on('system') else 0)
        self.setFixedSize(_W, max(1, h))
        self.setVisible(h > 0)

    # ── drawing ───────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        dark = is_dark()
        self._text = QColor('#f5f5f7') if dark else QColor('#1d1d1f')
        self._sub = QColor(235, 235, 245, 150) if dark else QColor(60, 60, 67, 160)
        self._line = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 20)
        self._track = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 22)
        base = QFont(self.font())
        self._f_head = QFont(base)
        self._f_head.setPointSizeF(10.5)
        self._f_head.setBold(True)
        self._f_key = QFont(base)
        self._f_key.setPointSizeF(10.5)
        self._f_val = QFont(base)
        self._f_val.setPointSizeF(10.5)
        self._f_val.setWeight(QFont.DemiBold)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = 0
        s = self._s
        for name, height in self._sections():
            p.setPen(self._line)
            p.drawLine(_PAD, y + 1, _W - _PAD, y + 1)
            getattr(self, f'_draw_{name}')(p, y + 4, s)
            y += height + _GAP
        # footer: OS and uptime
        if not self._on('system'):
            p.end()
            return
        p.setFont(self._f_key)
        p.setPen(self._sub)
        foot = '  ·  '.join(x for x in (s.get('os'), f"up {s['uptime']}" if s.get('uptime') else '') if x)
        p.drawText(QRectF(_PAD, y, _W - 2 * _PAD, _ROW), Qt.AlignVCenter | Qt.AlignLeft, foot)
        p.end()

    def _head(self, p, y, title: str, right: str = '') -> None:
        p.setFont(self._f_head)
        p.setPen(self._text)
        p.drawText(QRectF(_PAD, y, _W - 2 * _PAD, _HEAD), Qt.AlignVCenter | Qt.AlignLeft, title)
        if right:
            p.setFont(self._f_key)
            p.setPen(self._sub)
            p.drawText(QRectF(_PAD, y, _W - 2 * _PAD, _HEAD), Qt.AlignVCenter | Qt.AlignRight, right)

    def _pair(self, p, y, col: int, key: str, value: str, color: QColor = None) -> None:
        """One 'key  value' cell; col 0 = left half, 1 = right half."""
        half = (_W - 2 * _PAD) / 2
        x = _PAD + col * (half + 8)
        w = half - 8
        p.setFont(self._f_key)
        p.setPen(self._sub)
        p.drawText(QRectF(x, y, w, _ROW), Qt.AlignVCenter | Qt.AlignLeft, key)
        p.setFont(self._f_val)
        p.setPen(color or self._text)
        p.drawText(QRectF(x, y, w, _ROW), Qt.AlignVCenter | Qt.AlignRight, value)

    # ── sections ──────────────────────────────────────────────────────────────

    def _draw_cpu(self, p, y, s) -> None:
        load = s.get('load')
        self._head(p, y, f"CPU  ·  {s.get('layout', '')}".rstrip(' ·'),
                   'load ' + '  '.join(f'{v:.1f}' for v in load) if load else '')
        cores = s.get('cores') or []
        if not cores:
            return
        top = y + _HEAD + 2
        gap = 3.0
        width = (_W - 2 * _PAD - gap * (len(cores) - 1)) / len(cores)
        for i, pct in enumerate(cores):
            x = _PAD + i * (width + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(self._track)
            p.drawRoundedRect(QRectF(x, top, width, _CORE_H), 3, 3)
            h = max(2.0, _CORE_H * min(pct, 100.0) / 100.0)
            p.setBrush(_RED if pct >= 85 else _ORANGE if pct >= 60 else _BLUE)
            p.drawRoundedRect(QRectF(x, top + _CORE_H - h, width, h), 3, 3)

    def _draw_mem(self, p, y, s) -> None:
        m = s.get('mem') or {}
        self._head(p, y, 'Memory', f"{_gb(m.get('total'))} total" if m else '')
        r = y + _HEAD
        self._pair(p, r, 0, 'Used', _gb(m.get('used')))
        self._pair(p, r, 1, 'Free', _gb(m.get('available')))
        pressure = m.get('pressure')
        color = {'Normal': _GREEN, 'Warning': _ORANGE, 'Critical': _RED}.get(pressure)
        self._pair(p, r + _ROW, 0, 'Swap', _gb(m.get('swap')))
        self._pair(p, r + _ROW, 1, 'Pressure', pressure or '—', color)

    def _draw_disk(self, p, y, s) -> None:
        d = s.get('disk') or {}
        pct = f"{100 * d['used'] / d['total']:.0f}% used" if d.get('total') else ''
        self._head(p, y, d.get('name') or 'Disk', pct)
        r = y + _HEAD
        self._pair(p, r, 0, 'Used', _gb(d.get('used')))
        self._pair(p, r, 1, 'Free', _gb(d.get('free')))
        self._pair(p, r + _ROW, 0, 'Read', _rate(self._disk_r * 1024))
        self._pair(p, r + _ROW, 1, 'Write', _rate(self._disk_w * 1024))

    def _draw_net(self, p, y, s) -> None:
        self._head(p, y, 'Network')
        r = y + _HEAD
        self._pair(p, r, 0, 'Local', s.get('local_ip') or '—')
        self._pair(p, r, 1, 'Public', s.get('public_ip') or '…')

    def _draw_battery(self, p, y, s) -> None:
        b = s.get('battery') or {}
        pct = b.get('percent')
        status = ('Charging' if b.get('plugged') and (pct or 0) < 100 else
                  'Plugged in' if b.get('plugged') else 'On battery')
        self._head(p, y, 'Battery', _duration(b.get('secs_left')) if not b.get('plugged') else '')
        r = y + _HEAD
        level_color = _RED if pct is not None and pct <= 15 else None
        self._pair(p, r, 0, 'Level', '—' if pct is None else f'{pct:.0f}%', level_color)
        self._pair(p, r, 1, 'Status', status, _GREEN if b.get('plugged') else None)
        health = b.get('health')
        self._pair(p, r + _ROW, 0, 'Health', '—' if health is None else f'{health}%')
        cycles = b.get('cycles')
        self._pair(p, r + _ROW, 1, 'Cycles', '—' if cycles is None else str(cycles))

    def _draw_top(self, p, y, s) -> None:
        self._head(p, y, 'Busiest apps', 'CPU')
        rows = s.get('top') or []
        if not rows:
            p.setFont(self._f_key)
            p.setPen(self._sub)
            p.drawText(QRectF(_PAD, y + _HEAD, _W - 2 * _PAD, _ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       'Measuring…')
            return
        for i in range(3):
            r = y + _HEAD + i * _ROW
            if i < len(rows):
                name, pct = rows[i]
                p.setFont(self._f_key)
                p.setPen(self._text)
                fm = p.fontMetrics()
                name = fm.elidedText(str(name), Qt.ElideRight, _W - 2 * _PAD - 60)
                p.drawText(QRectF(_PAD, r, _W - 2 * _PAD, _ROW), Qt.AlignVCenter | Qt.AlignLeft, name)
                p.setFont(self._f_val)
                p.drawText(QRectF(_PAD, r, _W - 2 * _PAD, _ROW), Qt.AlignVCenter | Qt.AlignRight,
                           f'{pct:.1f}%')
