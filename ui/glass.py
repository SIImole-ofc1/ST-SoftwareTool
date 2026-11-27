"""
macOS Glass theme — a frosted, glossy look made for Mac users.

Two parts:

* ``MacBackdrop`` puts a native ``NSVisualEffectView`` (the blur macOS uses for
  sidebars and Control Center) behind the Qt window, through
  ``core.mac_appkit`` (ctypes, so no extra package is needed). The desktop
  Performance widget uses the same blur, with its own gloss / blur settings.
* ``build_qss()`` builds the stylesheet. Two user settings drive it:
    - gloss (0–100): how strong the light highlights on buttons, tabs and
      panels are — 0 is flat and matte, 100 is a shiny, Aqua-style split shine.
    - blur  (0–100): how strongly the desktop behind the window is frosted —
      0 is clear (tinted) glass, 100 is fully frosted.

On Windows and Linux there is no native blur behind the window, so the theme
draws the same glossy panels over a solid gradient backdrop instead.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from core import mac_appkit

IS_MAC = sys.platform == "darwin"

THEME_KEY     = "glass"
DEFAULT_GLOSS = 60          # app window (macOS Glass theme)
DEFAULT_BLUR  = 70
WIDGET_DEFAULT_GLOSS = 55   # desktop Performance widget
WIDGET_DEFAULT_BLUR  = 80


def _pct(settings: dict, key: str, default: int) -> int:
    try:
        return max(0, min(100, int(settings.get(key, default))))
    except (TypeError, ValueError):
        return default


def settings_values(settings: dict) -> tuple[int, int]:
    """(gloss, blur) for the app window, clamped to 0–100."""
    return (_pct(settings, "glass_gloss", DEFAULT_GLOSS),
            _pct(settings, "glass_blur", DEFAULT_BLUR))


def widget_values(settings: dict) -> tuple[int, int]:
    """(gloss, blur) for the desktop Performance widget, clamped to 0–100."""
    return (_pct(settings, "widget_gloss", WIDGET_DEFAULT_GLOSS),
            _pct(settings, "widget_blur", WIDGET_DEFAULT_BLUR))


def is_dark() -> bool:
    """True when the system is in Dark Mode (the glass theme follows it)."""
    try:
        return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
    except Exception:
        return True


# ── native blur (macOS) ──────────────────────────────────────────────────────

def native_blur_supported() -> bool:
    return mac_appkit.blur_supported()


def system_accent(dark: bool) -> str:
    """The user's macOS accent colour (System Settings › Appearance), or Apple blue."""
    return mac_appkit.accent_color() or ("#0a84ff" if dark else "#007aff")


class MacBackdrop:
    """Frosted glass behind the main window (the macOS Glass theme)."""

    def __init__(self, widget):
        self._widget = widget
        self._blur = mac_appkit.BlurBackdrop(material="window")

    @property
    def attached(self) -> bool:
        return self._blur.attached

    @property
    def native(self) -> mac_appkit.BlurBackdrop:
        return self._blur

    def apply(self, enabled: bool, blur: int, dark: bool) -> bool:
        """Show / hide the backdrop. Returns True when native blur is active."""
        if not enabled:
            self._blur.update(False, transparent_titlebar=False)
            return False
        if not self._blur.attach(int(self._widget.winId())):
            return False
        return self._blur.update(True, blur_alpha(blur), dark, transparent_titlebar=True)


def rounded_mask_png(radius: float, scale: float = 2.0) -> bytes:
    """A small 9-slice PNG of a rounded rectangle, used to give the native blur round
    corners (NSVisualEffectView.maskImage)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
    side = int((2 * radius + 2) * scale)
    img = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), radius * scale, radius * scale)
    p.fillPath(path, QColor(0, 0, 0))
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


# ── palettes ─────────────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {max(0, min(255, round(alpha * 255)))})"


def _mix(a: str, b: str, t: float) -> str:
    """Blend hex colour a towards b by t (0–1)."""
    a, b = a.lstrip("#"), b.lstrip("#")
    out = []
    for i in (0, 2, 4):
        x, y = int(a[i:i + 2], 16), int(b[i:i + 2], 16)
        out.append(f"{round(x + (y - x) * t):02x}")
    return "#" + "".join(out)


def palette(dark: bool, accent: str) -> dict:
    """Base colours for the glass theme (also used by the terminal and graphs)."""
    if dark:
        return {
            "dark": True, "accent": accent,
            "base": "#1c1c1f", "base_hi": "#2c2c31",   # window tint, top of gradient
            "solid": "#26262a",                        # menus, dialogs, popups
            "text": "#f5f5f7", "text2": "#a1a1a8", "text3": "#6e6e75",
            "light": "#ffffff", "shade": "#000000",
            "panel": 0.055, "edge": 0.10, "field": 0.28,
            "green": "#32d74b", "red": "#ff453a", "yellow": "#ffd60a",
        }
    return {
        "dark": False, "accent": accent,
        "base": "#ececef", "base_hi": "#fafafc",
        "solid": "#f6f6f8",
        "text": "#1d1d1f", "text2": "#55555c", "text3": "#8e8e93",
        "light": "#ffffff", "shade": "#000000",
        "panel": 0.42, "edge": 0.11, "field": 0.65,
        "green": "#248a3d", "red": "#d70015", "yellow": "#a05a00",
    }


def blur_alpha(blur: int) -> float:
    """Opacity of the native blur view for a 0–100 blur setting. Eased, because
    even a little sharp desktop showing through is very noticeable — this keeps
    the slider feeling even across its whole range."""
    b = max(0.0, min(1.0, blur / 100.0))
    return 1.0 - (1.0 - b) ** 2


def window_tint_alpha(blur: int, native: bool) -> float:
    """How opaque the window tint is. Less blur → more tint, so text stays readable
    even when the desktop shows through sharply."""
    if not native:
        return 1.0
    return 0.36 + 0.46 * (1.0 - blur_alpha(blur))


# ── stylesheet ───────────────────────────────────────────────────────────────

def _gloss_gradient(base_rgba: tuple, g: float, light: str, strength: float = 1.0) -> str:
    """A vertical gradient with an Aqua-style highlight split at the middle.
    base_rgba = (hex, alpha). g = gloss 0–1."""
    hexc, a = base_rgba
    s = g * strength
    top  = _rgba(_mix(hexc, light, 0.60 * s), min(1.0, a + 0.24 * s))
    mid  = _rgba(_mix(hexc, light, 0.25 * s), min(1.0, a + 0.09 * s))
    low  = _rgba(_mix(hexc, "#000000", 0.06 * s), max(0.0, a - 0.02 * s))
    bot  = _rgba(_mix(hexc, light, 0.15 * s), min(1.0, a + 0.05 * s))
    split = 0.5 if s > 0.05 else 0.0
    if split == 0.0:
        return _rgba(hexc, a)
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:0.49 {mid}, "
            f"stop:0.5 {low}, stop:1 {bot})")


def build_qss(gloss: int, blur: int, native: bool, dark: bool | None = None,
              accent: str | None = None) -> str:
    dark = is_dark() if dark is None else dark
    p = palette(dark, accent or system_accent(dark))
    g = max(0, min(100, gloss)) / 100.0
    A, L, S = p["accent"], p["light"], p["shade"]

    tint = window_tint_alpha(blur, native)
    win_top = _rgba(_mix(p["base"], p["base_hi"], 0.4 + 0.6 * g), tint)
    win_bot = _rgba(p["base"], tint)
    window_bg = f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {win_top}, stop:1 {win_bot})"

    panel      = _rgba(L, p["panel"])
    panel_hi   = _rgba(L, p["panel"] + (0.05 if dark else 0.15) * g)
    edge       = _rgba(S if not dark else L, p["edge"])
    edge_top   = _rgba(L, (0.10 if dark else 0.55) + (0.22 if dark else 0.40) * g)
    edge_ctl   = _rgba(L if dark else S, 0.32 if dark else 0.22)   # checkbox / radio rims
    field      = _rgba(S, p["field"]) if dark else _rgba(L, p["field"])
    hover      = _rgba(L, 0.08 if dark else 0.30)
    sel        = _rgba(A, 0.38 if dark else 0.24)

    btn_base   = ("#ffffff", 0.10) if dark else ("#ffffff", 0.72)
    btn        = _gloss_gradient(btn_base, g, L)
    btn_hover  = _gloss_gradient((btn_base[0], btn_base[1] + 0.06), g, L)
    btn_press  = _rgba(L, 0.05) if dark else _rgba(S, 0.06)
    accent_btn = _gloss_gradient((A, 1.0), g, L, 0.9)
    accent_hov = _gloss_gradient((_mix(A, L, 0.12), 1.0), g, L, 0.9)
    knob       = _gloss_gradient(("#ffffff", 1.0), g, "#ffffff")
    knob_edge  = _rgba(S, 0.25 if dark else 0.18)

    text, text2, text3 = p["text"], p["text2"], p["text3"]
    solid = p["solid"]

    return f"""
        * {{ outline: 0; }}
        QWidget               {{ background: transparent; color: {text}; }}
        QMainWindow           {{ background: {window_bg}; }}
        QDialog, QMessageBox  {{ background: {solid}; color: {text}; }}
        QToolTip              {{ background: {solid}; color: {text}; border: 1px solid {edge}; padding: 4px 6px; }}

        QMenuBar              {{ background: transparent; color: {text}; }}
        QMenuBar::item:selected {{ background: {hover}; border-radius: 5px; }}
        QMenu                 {{ background: {solid}; color: {text}; border: 1px solid {edge}; padding: 4px; }}
        QMenu::item           {{ padding: 4px 18px; border-radius: 5px; }}
        QMenu::item:selected  {{ background: {A}; color: #ffffff; }}
        QMenu::separator      {{ height: 1px; background: {edge}; margin: 4px 8px; }}

        QStatusBar            {{ background: transparent; color: {text2}; font-size: 11px; border-top: 1px solid {edge}; }}
        QStatusBar QLabel     {{ color: {text2}; }}

        QTabWidget::pane      {{ background: {panel}; border: 1px solid {edge}; border-top-color: {edge_top};
                                 border-radius: 12px; top: -1px; }}
        QTabWidget::tab-bar   {{ left: 8px; }}
        QTabBar               {{ background: transparent; }}
        QTabBar::tab          {{ background: transparent; color: {text2}; padding: 5px 13px; margin: 4px 2px 5px 2px;
                                 border: 1px solid transparent; border-radius: 8px; }}
        QTabBar::tab:hover    {{ background: {hover}; color: {text}; }}
        QTabBar::tab:selected {{ background: {accent_btn}; color: #ffffff; border: 1px solid {_rgba(L, 0.18 + 0.25 * g)}; }}

        QGroupBox             {{ background: {panel}; border: 1px solid {edge}; border-top-color: {edge_top};
                                 border-radius: 12px; margin-top: 20px; padding: 10px 8px 8px 8px; font-weight: 600; }}
        QGroupBox::title      {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; top: 2px;
                                 color: {text2}; }}

        QListView, QTreeView, QTableView {{ background: {solid}; color: {text}; border: 1px solid {edge};
                                 border-radius: 8px; selection-background-color: {A}; selection-color: #ffffff; }}
        QListWidget, QTreeWidget, QTableView, QTreeView {{ background: {panel}; color: {text};
                                 border: 1px solid {edge}; border-radius: 8px; alternate-background-color: {panel_hi};
                                 gridline-color: transparent; }}
        QListWidget::item, QTreeWidget::item, QTableView::item {{ padding: 2px 4px; border-radius: 5px; }}
        QListWidget::item:hover, QTreeWidget::item:hover, QTreeView::item:hover, QTableView::item:hover
                              {{ background: {hover}; }}
        QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected, QTableView::item:selected
                              {{ background: {sel}; color: {text}; }}
        QHeaderView           {{ background: transparent; }}
        QHeaderView::section  {{ background: transparent; color: {text2}; border: none;
                                 border-bottom: 1px solid {edge}; border-right: 1px solid {edge}; padding: 4px 8px; }}
        QTableCornerButton::section {{ background: transparent; border: none; }}

        QTextEdit, QPlainTextEdit {{ background: {field}; color: {text}; border: 1px solid {edge};
                                 border-radius: 8px; selection-background-color: {A}; }}
        QLineEdit             {{ background: {field}; color: {text}; border: 1px solid {edge};
                                 border-radius: 7px; padding: 4px 8px; selection-background-color: {A}; }}
        QLineEdit:focus       {{ border: 2px solid {_rgba(A, 0.85)}; padding: 3px 7px; }}
        QLineEdit::placeholder-text {{ color: {text3}; }}

        QPushButton           {{ background: {btn}; color: {text}; border: 1px solid {edge};
                                 border-top-color: {edge_top}; border-radius: 7px; padding: 5px 14px; }}
        QPushButton:hover     {{ background: {btn_hover}; }}
        QPushButton:pressed   {{ background: {btn_press}; }}
        QPushButton:checked   {{ background: {accent_btn}; color: #ffffff; }}
        QPushButton:disabled  {{ color: {text3}; background: {_rgba(L, 0.04 if dark else 0.35)}; }}
        QPushButton#primaryButton {{ background: {accent_btn}; color: #ffffff; font-weight: 600;
                                 border: 1px solid {_rgba(L, 0.18 + 0.25 * g)}; }}
        QPushButton#primaryButton:hover {{ background: {accent_hov}; }}

        QComboBox             {{ background: {btn}; color: {text}; border: 1px solid {edge};
                                 border-top-color: {edge_top}; border-radius: 7px; padding: 4px 10px; }}
        QComboBox::drop-down  {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{ background: {solid}; color: {text}; border: 1px solid {edge};
                                 selection-background-color: {A}; selection-color: #ffffff; }}

        QRadioButton, QCheckBox {{ color: {text}; spacing: 7px; background: transparent; }}
        QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid {edge_ctl};
                                 background: {btn}; }}
        QRadioButton::indicator:checked {{ border: 5px solid {A}; background: #ffffff; width: 6px; height: 6px; }}
        QCheckBox::indicator  {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {edge_ctl};
                                 background: {btn}; }}
        QCheckBox::indicator:checked {{ background: {accent_btn}; border: 1px solid {A}; }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {_rgba(L, 0.04)}; }}

        QLabel                {{ color: {text}; background: transparent; }}
        QLabel:disabled       {{ color: {text3}; }}

        QSlider::groove:horizontal {{ height: 4px; background: {_rgba(L if dark else S, 0.16)}; border-radius: 2px; margin: 7px 0; }}
        QSlider::sub-page:horizontal {{ background: {A}; border-radius: 2px; }}
        QSlider::add-page:horizontal {{ background: {_rgba(L if dark else S, 0.16)}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {knob}; border: 1px solid {knob_edge};
                                 width: 18px; height: 18px; border-radius: 9px; margin: -8px 0; }}
        QSlider::groove:vertical {{ width: 4px; background: {_rgba(L if dark else S, 0.16)}; border-radius: 2px; margin: 0 7px; }}
        QSlider::handle:vertical {{ background: {knob}; border: 1px solid {knob_edge};
                                 width: 18px; height: 18px; border-radius: 9px; margin: 0 -8px; }}
        QSlider::sub-page:horizontal:disabled {{ background: {_rgba(L if dark else S, 0.25)}; }}

        QProgressBar          {{ background: {_rgba(L if dark else S, 0.10)}; border: none; color: {text};
                                 text-align: center; border-radius: 5px; min-height: 10px; }}
        QProgressBar::chunk   {{ background: {accent_btn}; border-radius: 5px; }}

        QSplitter::handle     {{ background: transparent; }}
        QScrollArea           {{ background: transparent; border: none; }}
        QScrollBar:vertical   {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {_rgba(L if dark else S, 0.28)}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::handle:vertical:hover {{ background: {_rgba(L if dark else S, 0.45)}; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle:horizontal {{ background: {_rgba(L if dark else S, 0.28)}; border-radius: 3px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QDialogButtonBox QPushButton {{ min-width: 70px; }}
    """


# ── helpers for the other themed widgets ─────────────────────────────────────

def terminal_palette(dark: bool | None = None) -> dict:
    """Colours for TerminalWidget. bg / input_bg / border are QSS colours."""
    dark = is_dark() if dark is None else dark
    p = palette(dark, system_accent(dark))
    return {
        "bg":       _rgba(p["shade"], 0.20) if dark else _rgba(p["light"], 0.40),
        "input_bg": _rgba(p["light"], 0.06) if dark else _rgba(p["light"], 0.45),
        "border":   _rgba(p["light"] if dark else p["shade"], 0.12),
        "text":     p["text"],
        "prompt":   p["accent"],
        "success":  p["green"],
        "error":    p["red"],
        "info":     p["text2"],
        "warn":     p["yellow"],
        "select":   _rgba(p["accent"], 0.45),
    }


def graph_colors(dark: bool | None = None) -> dict:
    """Chrome colours for UsageGraph (#AARRGGBB strings, read by QColor)."""
    dark = is_dark() if dark is None else dark
    if dark:
        return {"bg": "#38000000", "grid": "#16ffffff", "axis": "#80ffffff",
                "border": "#24ffffff", "subtitle": "#99ffffff", "val": "#ffffff",
                "fill_a": 90, "fill_b": 4, "radius": 10}
    return {"bg": "#80ffffff", "grid": "#14000000", "axis": "#8c000000",
            "border": "#1f000000", "subtitle": "#99000000", "val": "#1d1d1f",
            "fill_a": 110, "fill_b": 10, "radius": 10}


def sidebar_qss(dark: bool | None = None) -> str:
    """Device Settings sidebar (Windows-only view, kept consistent anyway)."""
    dark = is_dark() if dark is None else dark
    p = palette(dark, system_accent(dark))
    return (
        f'QListWidget {{ background: {_rgba(p["light"], p["panel"])}; border: none;'
        f'  border-right: 1px solid {_rgba(p["light"] if dark else p["shade"], 0.10)};'
        f'  font-size: 12px; color: {p["text2"]}; padding: 6px; }}'
        f'QListWidget::item {{ padding: 8px 12px; border-radius: 7px; margin: 1px 0; }}'
        f'QListWidget::item:selected {{ background: {p["accent"]}; color: #ffffff; }}'
        f'QListWidget::item:hover:!selected {{ background: {_rgba(p["light"], 0.08 if dark else 0.30)};'
        f'  color: {p["text"]}; }}'
    )
