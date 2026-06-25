"""
Self-contained rolling graph widget (QPainter) and a detail dialog.
Supports four themes that match the rest of the UI.
"""
from collections import deque

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QPainter, QPen, QColor, QPainterPath, QFont, QBrush, QLinearGradient,
)

# ── per-theme chrome colours ──────────────────────────────────────────────────
_TC = {
    "dark": {
        "bg":       "#050505",
        "grid":     "#181818",
        "axis":     "#3a3a3a",
        "border":   "#202020",
        "subtitle": "#555555",
        "val":      "#ffffff",
        "fill_a":   80,
        "fill_b":   6,
    },
    "dark_bw": {
        "bg":       "#050505",
        "grid":     "#1e1e1e",
        "axis":     "#444444",
        "border":   "#2a2a2a",
        "subtitle": "#555555",
        "val":      "#ffffff",
        "fill_a":   80,
        "fill_b":   6,
    },
    "light": {
        "bg":       "#d0d0d0",
        "grid":     "#b8b8b8",
        "axis":     "#444444",
        "border":   "#909090",
        "subtitle": "#555555",
        "val":      "#111111",
        "fill_a":   100,
        "fill_b":   20,
    },
    "hc": {
        "bg":       "#000000",
        "grid":     "#2a2a2a",
        "axis":     "#666666",
        "border":   "#ffffff",
        "subtitle": "#888888",
        "val":      "#ffff00",
        "fill_a":   90,
        "fill_b":   10,
    },
    "win95": {
        "bg":       "#000080",
        "grid":     "#00005a",
        "axis":     "#c0c0c0",
        "border":   "#c0c0c0",
        "subtitle": "#8080ff",
        "val":      "#ffff00",
        "fill_a":   70,
        "fill_b":   8,
    },
}


class UsageGraph(QWidget):
    """Scrolling time-series graph, 0–100%.  Call push() to add a sample."""

    MAX_HISTORY = 60

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self.title        = title
        self.line_color   = QColor(color)
        self._history     = deque([0.0] * self.MAX_HISTORY, maxlen=self.MAX_HISTORY)
        self.current      = 0.0
        self.subtitle     = ""
        self._tc          = _TC["dark"]          # theme chrome colours
        self._detail: "GraphDetailDialog | None" = None
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    # ── theme ─────────────────────────────────────────────────────────────────

    def set_theme(self, theme: str):
        self._tc = _TC.get(theme, _TC["dark"])
        self.update()
        if self._detail:
            self._detail.set_theme(theme)

    # ── data ──────────────────────────────────────────────────────────────────

    def push(self, value: float, subtitle: str = ""):
        self.current  = max(0.0, min(100.0, float(value)))
        self.subtitle = subtitle
        self._history.append(self.current)
        self.update()
        if self._detail and self._detail.isVisible():
            self._detail.push(self.current, subtitle)

    def open_detail(self):
        if self._detail is None:
            self._detail = GraphDetailDialog(
                self.title, self.line_color.name(), self._tc, parent=self.window()
            )
            for v in self._history:
                self._detail._push_raw(v)
            self._detail._graph.update()
        self._detail.show()
        self._detail.raise_()
        self._detail.activateWindow()

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _draw(p, self.width(), self.height(), self._history, self.current,
              self.title, self.subtitle, self.line_color, self._tc)
        p.end()


# ── shared draw routine ───────────────────────────────────────────────────────

def _draw(p: QPainter, w: int, h: int, history, current: float,
          title: str, subtitle: str, color: QColor, tc: dict):
    PT, PB, PL, PR = 22, 18, 34, 4
    gw = w - PL - PR
    gh = h - PT - PB

    p.fillRect(0, 0, w, h, QColor(tc["bg"]))

    small_font = QFont("Consolas", 7)
    p.setFont(small_font)
    for pct in (0, 25, 50, 75, 100):
        gy = PT + int(gh * (1 - pct / 100))
        p.setPen(QPen(QColor(tc["grid"]), 1))
        p.drawLine(PL, gy, PL + gw, gy)
        p.setPen(QPen(QColor(tc["axis"])))
        p.drawText(0, gy - 5, PL - 3, 14,
                   Qt.AlignRight | Qt.AlignVCenter, str(pct))

    p.setPen(QPen(QColor(tc["border"]), 1))
    p.drawRect(PL, PT, gw, gh)

    pts = list(history)
    n   = len(pts)
    if n >= 2:
        coords = []
        for i, v in enumerate(pts):
            cx = PL + int(gw * i / (n - 1))
            cy = PT + int(gh * (1.0 - v / 100.0))
            cy = max(PT, min(PT + gh, cy))
            coords.append((cx, cy))

        grad = QLinearGradient(0, PT, 0, PT + gh)
        c1 = QColor(color); c1.setAlpha(tc["fill_a"])
        c2 = QColor(color); c2.setAlpha(tc["fill_b"])
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)

        path = QPainterPath()
        path.moveTo(coords[0][0], PT + gh)
        for cx, cy in coords:
            path.lineTo(cx, cy)
        path.lineTo(coords[-1][0], PT + gh)
        path.closeSubpath()
        p.fillPath(path, QBrush(grad))

        p.setPen(QPen(color, 1.5))
        for i in range(1, len(coords)):
            p.drawLine(coords[i-1][0], coords[i-1][1],
                       coords[i][0],   coords[i][1])

    # Title
    p.setPen(QPen(color))
    p.setFont(QFont("Consolas", 8, QFont.Bold))
    p.drawText(PL + 3, 15, title)

    # Current value
    val_str = f"{current:.0f}%"
    p.setPen(QPen(QColor(tc["val"])))
    p.setFont(QFont("Consolas", 11, QFont.Bold))
    fm = p.fontMetrics()
    p.drawText(w - fm.horizontalAdvance(val_str) - PR - 3, 16, val_str)

    # Subtitle
    if subtitle:
        p.setPen(QPen(QColor(tc["subtitle"])))
        p.setFont(small_font)
        p.drawText(PL + 3, h - 4, subtitle)


# ── detail dialog ─────────────────────────────────────────────────────────────

class GraphDetailDialog(QDialog):
    """Full-size (~720×460) graph with rolling 5-minute history and stats."""

    MAX_HISTORY = 300

    def __init__(self, title: str, color: str, tc: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title}  —  Detail")
        self.resize(720, 460)
        self.setMinimumSize(480, 300)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._graph = UsageGraph(title, color)
        self._graph.MAX_HISTORY = self.MAX_HISTORY
        self._graph._history    = deque([0.0] * self.MAX_HISTORY,
                                        maxlen=self.MAX_HISTORY)
        self._graph._tc = tc
        layout.addWidget(self._graph)

        stats_row = QHBoxLayout()
        self._min_lbl = QLabel("Min: --")
        self._max_lbl = QLabel("Max: --")
        self._avg_lbl = QLabel("Avg: --")
        self._stat_labels = (self._min_lbl, self._max_lbl, self._avg_lbl)
        self._apply_stat_style(tc)
        for lbl in self._stat_labels:
            stats_row.addWidget(lbl)
        stats_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        stats_row.addWidget(close_btn)
        layout.addLayout(stats_row)

    def _apply_stat_style(self, tc: dict):
        color = tc.get("axis", "#aaaaaa")
        ss = f"color:{color}; font-family:Consolas; font-size:11px;"
        for lbl in self._stat_labels:
            lbl.setStyleSheet(ss)

    def set_theme(self, theme: str):
        tc = _TC.get(theme, _TC["dark"])
        self._graph._tc = tc
        self._graph.update()
        self._apply_stat_style(tc)

    def _push_raw(self, value: float):
        self._graph._history.append(value)
        self._graph.current = value

    def push(self, value: float, subtitle: str = ""):
        self._push_raw(value)
        self._graph.subtitle = subtitle
        self._graph.update()
        vals = [v for v in self._graph._history if v > 0]
        if vals:
            self._min_lbl.setText(f"Min: {min(vals):.1f}%")
            self._max_lbl.setText(f"Max: {max(vals):.1f}%")
            self._avg_lbl.setText(f"Avg: {sum(vals)/len(vals):.1f}%")
