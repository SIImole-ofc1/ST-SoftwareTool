from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, QCompleter, QFrame
from PySide6.QtCore import Qt, Signal, QEvent, QThread
from PySide6.QtGui import QFont, QColor, QTextCursor, QFontMetrics
import os
import subprocess
import sys
from core.osinfo import IS_MAC, IS_WINDOWS, NO_WINDOW
from .shell_runner import ShellRunner, needs_real_terminal
from core.updater import APP_VERSION
from . import glass

# Dark  —  Black & Green
_D = {
    "bg":      "#000000",
    "input_bg":"#0a0a0a",
    "border":  "#1a3a1a",
    "text":    "#00ff41",
    "prompt":  "#00ff41",
    "success": "#00cc33",
    "error":   "#ff3333",
    "info":    "#4a7a4a",
    "warn":    "#ccaa00",
}

# Dark  —  Black & White
_DW = {
    "bg":      "#000000",
    "input_bg":"#0d0d0d",
    "border":  "#333333",
    "text":    "#ffffff",
    "prompt":  "#ffffff",
    "success": "#cccccc",
    "error":   "#ff4444",
    "info":    "#888888",
    "warn":    "#ffcc00",
}

# Light  —  Black on Grey
_L = {
    "bg":      "#c0c0c0",
    "input_bg":"#b0b0b0",
    "border":  "#808080",
    "text":    "#000000",
    "prompt":  "#000000",
    "success": "#004400",
    "error":   "#cc0000",
    "info":    "#333333",
    "warn":    "#886600",
}

# Windows 95  —  Classic
_W95 = {
    "bg":      "#000080",
    "input_bg":"#00006a",
    "border":  "#c0c0c0",
    "text":    "#c0c0c0",
    "prompt":  "#ffff00",
    "success": "#00ff00",
    "error":   "#ff6666",
    "info":    "#00ffff",
    "warn":    "#ffff00",
}

# High Contrast
_HC = {
    "bg":      "#000000",
    "input_bg":"#000000",
    "border":  "#ffffff",
    "text":    "#ffffff",
    "prompt":  "#ffff00",
    "success": "#00ff00",
    "error":   "#ff0000",
    "info":    "#00ffff",
    "warn":    "#ff8800",
}

_PALETTES = {
    "dark":    _D,
    "dark_bw": _DW,
    "light":   _L,
    "hc":      _HC,
    "win95":   _W95,
}

_FONT = QFont("Consolas", 10)

# Padded to the box's inner width so the right border stays lined up for any version number
_BANNER_TITLE = f"ST-SoftwareTool  v{APP_VERSION}".ljust(29)

_BANNER = f"""\
  ╔══════════════════════════════════════════════════╗
  ║                                                  ║
  ║   ██████╗ ████████╗                              ║
  ║  ██╔════╝    ██╔══╝                              ║
  ║   ╚█████╗    ██║    {_BANNER_TITLE}║
  ║    ╚═══██╗   ██║    For Windows and macOS        ║
  ║  ██████╔╝    ██║                                 ║
  ║  ╚═════╝     ╚═╝                                 ║
  ║                                                  ║
  ╚══════════════════════════════════════════════════╝"""


class _YtWorker(QThread):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, url: str, dest_dir: str, exe_path: str):
        super().__init__()
        self._url = url
        self._dest_dir = dest_dir
        self._exe = exe_path

    def run(self):
        if not os.path.exists(self._exe):
            self.finished.emit(False, f"yt-dlp not found at: {self._exe}")
            return

        outtmpl = os.path.join(self._dest_dir, "%(title)s.%(ext)s")
        cmd = [
            self._exe,
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--output", outtmpl,
            "--newline",
            "--no-warnings",
            "--",  # everything after this is a URL, never a yt-dlp option
            self._url,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=NO_WINDOW,
            )
            title = ""
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if "[download]" in line and "%" in line:
                    self.progress.emit(f"  {line.strip()}")
                elif "[download] Destination:" in line:
                    fname = os.path.basename(line.split("Destination:", 1)[1].strip())
                    if not title:  # keep first (video) filename, not the audio fragment
                        title = os.path.splitext(fname)[0]
                    self.progress.emit(f"  Saving: {fname}")
                elif "[Merger]" in line or "[ffmpeg]" in line:
                    self.progress.emit("  Merging audio and video...")
            proc.wait()
            if proc.returncode == 0:
                self.finished.emit(True, f"Done! Saved to Pictures: {title or 'video'}")
            else:
                self.finished.emit(False, "Download failed. Check the URL and try again.")
        except Exception as exc:
            self.finished.emit(False, f"Download error: {exc}")


_COMMAND_HINTS = [
    ">_/help:show",
    ">_/app:list",
    ">_/app:list_pinned",
    ">_/app:list_cat(\"\")",
    ">_/from:open_app(\"\")",
    ">_/from:add_app(\"\")",
    ">_/from:remove_app(\"\")",
    ">_/from:pin_app(\"\")",
    ">_/from:unpin_app(\"\")",
    ">_/from:rename_app(\"\", \"\")",
    ">_/find:search(\"\")",
    ">_/find:info(\"\")",
    ">_/cat:list",
    ">_/sys:scan",
    ">_/sys:clear",
    ">_/sys:exit",
    ">_/cut:close_app(\"\")",
    ">_/out:min_app(\"\")",
    ">_/gui:on_[True]",
    ">_/gui:on_[False]",
    ">_/look=gui:theme_1",
    ">_/look=gui:theme_2",
    ">_/look=gui:theme_3",
    ">_/look=gui:theme_4",
    ">_/look=gui:theme_5",
    ">_/look=gui:theme_6",
    ">_/brew:install",
    ">_/from_youtube+URL={}_download:to_Pictures",
]


class TerminalWidget(QWidget):
    switch_to_gui  = Signal()
    exit_app       = Signal()
    theme_changed  = Signal(str)

    def __init__(self, manager, processor, parent=None):
        super().__init__(parent)
        self.manager   = manager
        self.processor = processor
        self._history: list[str] = []
        self._hist_idx: int = 0
        self._theme = manager.settings.get("theme", "dark")
        self._palette = self._palette_for(self._theme)
        self._suggestions_enabled = manager.settings.get("terminal_suggestions", True)
        self._yt_workers: list = []  # keep references so GC doesn't kill active threads

        self._shell = ShellRunner(self)
        self._shell.output.connect(self._on_shell_output)
        self._shell.finished.connect(self._on_shell_finished)
        self._shell_stopped = False

        self._build_ui()
        self._setup_completer()
        self._apply_theme()
        self._show_banner()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Fixed-height banner — always pinned at top, never scrolls away
        self.banner_area = QTextEdit(readOnly=True)
        self.banner_area.setFont(_FONT)
        self.banner_area.setFrameShape(QFrame.NoFrame)
        self.banner_area.setCursorWidth(0)
        self.banner_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.banner_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        n_banner_lines = _BANNER.count('\n') + 1
        fm = QFontMetrics(_FONT)
        self.banner_area.setFixedHeight(fm.lineSpacing() * n_banner_lines + 18)
        root.addWidget(self.banner_area)

        # Scrollable command output
        self.output = QTextEdit(readOnly=True)
        self.output.setFont(_FONT)
        self.output.setFrameShape(QFrame.NoFrame)
        self.output.setCursorWidth(0)
        self.output.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.output.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.output)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(0)

        self.input_field = QLineEdit()
        self.input_field.setFont(_FONT)
        self.input_field.setPlaceholderText(
            "type a shell command (ls, cd, brew …)  or  >_/help:show")
        self.input_field.returnPressed.connect(self._on_enter)
        self.input_field.installEventFilter(self)
        bar.addWidget(self.input_field)

        root.addLayout(bar)

    def _setup_completer(self):
        self._completer = QCompleter(_COMMAND_HINTS, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchContains)
        if self._suggestions_enabled:
            self.input_field.setCompleter(self._completer)

    def set_suggestions_enabled(self, enabled: bool):
        self._suggestions_enabled = enabled
        self.input_field.setCompleter(self._completer if enabled else None)

    @staticmethod
    def _palette_for(theme: str) -> dict:
        if theme == glass.THEME_KEY:
            return glass.terminal_palette()
        return _PALETTES.get(theme, _D)

    def _apply_theme(self):
        p = self._palette
        if self._theme == glass.THEME_KEY:
            self._apply_glass_theme(p)
            return
        txt_style = f"""
            QTextEdit {{
                background-color: {p['bg']};
                color: {p['text']};
                border: none;
                padding: 8px;
            }}
        """
        self.banner_area.setStyleSheet(txt_style)
        self.output.setStyleSheet(txt_style)
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {p['input_bg']};
                color: {p['text']};
                border: none;
                border-top: 1px solid {p['border']};
                padding: 6px 10px;
                selection-background-color: {p['border']};
            }}
        """)
        self.setStyleSheet(f"background-color: {p['input_bg']};")

    def _apply_glass_theme(self, p: dict):
        # Transparent panes so the window's frosted glass shows through. Scoped by
        # object name so the colour doesn't stack up on every child widget.
        self.setObjectName("stTerminal")
        self.setStyleSheet(f"#stTerminal {{ background: transparent; }}")
        txt_style = f"""
            QTextEdit {{
                background: {p['bg']};
                color: {p['text']};
                border: none;
                padding: 10px 12px;
                selection-background-color: {p['select']};
            }}
        """
        self.banner_area.setStyleSheet(txt_style)
        self.output.setStyleSheet(txt_style)
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background: {p['input_bg']};
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 9px;
                padding: 7px 12px;
                selection-background-color: {p['select']};
            }}
            QLineEdit:focus {{ border: 1px solid {p['prompt']}; }}
        """)

    def set_theme(self, theme: str, keep_output: bool = False):
        self._theme = theme
        self._palette = self._palette_for(theme)
        self._apply_theme()
        if keep_output:
            self._render_banner()
        else:
            self._show_banner()  # re-render so text colors update (char format overrides stylesheet)

    def _show_banner(self):
        self._render_banner()
        self.output.clear()

    def _render_banner(self):
        self.banner_area.clear()
        cur = self.banner_area.textCursor()
        cur.movePosition(QTextCursor.End)
        self.banner_area.setTextCursor(cur)
        fmt = self.banner_area.currentCharFormat()
        fmt.setForeground(QColor(self._color("text")))
        self.banner_area.setCurrentCharFormat(fmt)
        self.banner_area.insertPlainText(_BANNER)

    # ── output helpers ────────────────────────────────────────────────────────

    def _color(self, kind: str) -> str:
        return self._palette.get(kind, self._palette["text"])

    def _write(self, text: str, color: str = ""):
        try:
            cur = self.output.textCursor()
            cur.movePosition(QTextCursor.End)
            self.output.setTextCursor(cur)
            fmt = self.output.currentCharFormat()
            fmt.setForeground(QColor(color or self._color("text")))
            self.output.setCurrentCharFormat(fmt)
            self.output.insertPlainText(text + "\n")
            sb = self.output.verticalScrollBar()
            sb.setValue(sb.maximum())
        except RuntimeError:
            pass  # widget was destroyed (e.g. app closed mid-download)

    # ── command handling ──────────────────────────────────────────────────────

    def _on_enter(self):
        raw = self.input_field.text().strip()
        if self._shell.is_running():
            # A command is running: the line is its input (e.g. an answer to y/n)
            self.input_field.clear()
            self._write(raw, self._color("info"))
            self._shell.send_input(raw)
            return
        if not raw:
            return
        self.input_field.clear()

        if not raw.startswith(">_/"):
            self._run_shell(raw)
            return

        if not (self._history and self._history[-1] == raw):
            self._history.append(raw)
        self._hist_idx = len(self._history)

        self._write(f"> {raw}", self._color("prompt"))

        result = self.processor.process_script(raw)

        action = result.action
        if action == "clear":
            self._show_banner()
            return
        if action == "switch_gui":
            if result.message:
                self._write(result.message, self._color("info"))
            self.switch_to_gui.emit()
            return
        if action == "exit":
            self._write(result.message, self._color("info"))
            self.exit_app.emit()
            return
        if action == "scan":
            self._write(result.message, self._color("info"))
            self._run_scan()
            return
        if action.startswith("theme_"):
            theme = action.split("_", 1)[1]
            self.set_theme(theme)
            self.theme_changed.emit(theme)
            return
        if action == "brew_install":
            self._install_homebrew()
            return
        if action == "youtube_download":
            self._write(result.message, self._color("info"))
            self._run_yt_download(result.data["url"])
            return

        if result.message:
            ink = self._color("text") if result.success else self._color("error")
            for line in result.message.splitlines():
                self._write(line, ink)

        self._write("")

    # ── shell commands ────────────────────────────────────────────────────────

    def _run_shell(self, command: str):
        if not (self._history and self._history[-1] == command):
            self._history.append(command)
        self._hist_idx = len(self._history)
        self._write(f"{self._shell.display_cwd()} $ {command}", self._color("prompt"))

        first = command.split()[0] if command.split() else ""
        if first in ("clear", "cls"):
            self._show_banner()
            return
        if IS_MAC and first == "brew":
            from core import homebrew
            if not homebrew.is_installed():
                self._write("Homebrew isn't installed yet.  Type  >_/brew:install  to install it "
                            "(or use Settings › Homebrew).", self._color("warn"))
                self._write("")
                return
        if needs_real_terminal(command):
            self._write(f"'{first}' needs a full terminal window — "
                        f"{self._shell.open_in_terminal(command)}", self._color("info"))
            self._write("")
            return
        self._shell_stopped = False
        self._shell.run(command)
        self.input_field.setPlaceholderText("running…  Ctrl+C stops it · Enter sends input")

    def _on_shell_output(self, text: str, is_err: bool):
        self._write(text, self._color("error") if is_err else self._color("text"))

    def _on_shell_finished(self, code: int):
        if self._shell_stopped:
            self._write("^C  stopped", self._color("warn"))
        elif code != 0:
            self._write(f"[exit {code}]", self._color("error"))
        self._write("")
        self.input_field.setPlaceholderText(
            "type a shell command (ls, cd, brew …)  or  >_/help:show")

    def _stop_shell(self):
        if self._shell.is_running():
            self._shell_stopped = True
            self._shell.stop()

    def _install_homebrew(self):
        from ui.homebrew_ui import install_homebrew
        install_homebrew(self, on_message=lambda ok, msg: (
            self._write(msg, self._color("success") if ok else self._color("error")),
            self._write("")))

    def _run_scan(self):
        try:
            self._write("Scanning registry, Start Menu and all user desktops…", self._color("info"))
            found = self.manager.scan_all()
            self._write(f"Found {len(found)} programs total.", self._color("info"))
            added = skipped = 0
            for name, path, cat in found:
                ok, _ = self.manager.add_app(name, path, cat)
                if ok:
                    added += 1
                else:
                    skipped += 1
            self._write(
                f"Imported {added} new apps. {skipped} already registered.",
                self._color("success"),
            )
        except Exception as exc:
            self._write(f"Scan error: {exc}", self._color("error"))
        self._write("")

    def _run_yt_download(self, url: str):
        if self._yt_workers:
            self._write("A download is already in progress. Please wait.", self._color("warn"))
            return

        pictures = os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(pictures, exist_ok=True)

        # Locate yt-dlp: next to the app binary when compiled, in tools/ in dev
        yt_name = "yt-dlp.exe" if IS_WINDOWS else "yt-dlp_macos" if IS_MAC else "yt-dlp"
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        exe_path = os.path.join(exe_dir, "tools", yt_name)
        if not os.path.exists(exe_path):
            exe_path = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "tools", yt_name)
            )
        if not os.path.exists(exe_path) and not IS_WINDOWS:
            import shutil
            exe_path = shutil.which("yt-dlp") or exe_path   # e.g. installed with Homebrew
        if os.path.exists(exe_path) and not IS_WINDOWS and not os.access(exe_path, os.X_OK):
            try:
                os.chmod(exe_path, 0o755)
            except OSError:
                pass

        worker = _YtWorker(url, pictures, exe_path)
        self._yt_workers.append(worker)

        def _on_progress(line: str):
            self._write(line, self._color("info"))

        def _on_done(success: bool, msg: str):
            ink = self._color("success") if success else self._color("error")
            self._write(msg, ink)
            self._write("")
            if worker in self._yt_workers:
                self._yt_workers.remove(worker)
            worker.deleteLater()

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_done)
        worker.start()

    # ── keyboard history navigation ───────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.input_field and event.type() == QEvent.KeyPress:
            key = event.key()
            # Ctrl+C (the Control key, also on a Mac keyboard) stops the running command
            mods = event.modifiers()
            ctrl = (mods & Qt.MetaModifier) if IS_MAC else (mods & Qt.ControlModifier)
            if key == Qt.Key_C and ctrl and self._shell.is_running():
                self._stop_shell()
                return True
            if key == Qt.Key_Up:
                if self._hist_idx > 0:
                    self._hist_idx -= 1
                    self.input_field.setText(self._history[self._hist_idx])
                return True
            if key == Qt.Key_Down:
                if self._hist_idx < len(self._history) - 1:
                    self._hist_idx += 1
                    self.input_field.setText(self._history[self._hist_idx])
                else:
                    self._hist_idx = len(self._history)
                    self.input_field.clear()
                return True
        return super().eventFilter(obj, event)

    def focus_input(self):
        self.input_field.setFocus()
