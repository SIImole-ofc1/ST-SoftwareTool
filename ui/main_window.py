import os
from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialogButtonBox,
)
from PySide6.QtGui import QAction, QKeySequence, QIcon, QFont, QPixmap
from PySide6.QtCore import Qt
from .terminal_widget import TerminalWidget
from .gui_view import GUIView


class MainWindow(QMainWindow):
    def __init__(self, manager, processor):
        super().__init__()
        self.manager   = manager
        self.processor = processor
        self.setWindowTitle("ST-SoftwareTool")
        self.setMinimumSize(820, 580)
        self.resize(960, 660)

        _icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "STsoftwareterminalLOGO.png.png")
        if os.path.exists(_icon):
            self.setWindowIcon(QIcon(_icon))

        self._build_menu()
        self._build_central()
        self._apply_theme()

        if manager.settings.get("default_view", "terminal") == "gui":
            self._go_gui()
        else:
            self._go_terminal()

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = self.menuBar()

        # File
        file_m = mb.addMenu("File")

        a_add = QAction("Add App…", self)
        a_add.setShortcut("Ctrl+N")
        a_add.triggered.connect(self._menu_add_app)
        file_m.addAction(a_add)

        file_m.addSeparator()

        a_exit = QAction("Exit", self)
        a_exit.setShortcut("Alt+F4")
        a_exit.triggered.connect(self.close)
        file_m.addAction(a_exit)

        # View
        view_m = mb.addMenu("View")

        a_term = QAction("Terminal Mode", self)
        a_term.setShortcut("Ctrl+1")
        a_term.triggered.connect(self._go_terminal)
        view_m.addAction(a_term)

        a_gui = QAction("GUI Mode", self)
        a_gui.setShortcut("Ctrl+2")
        a_gui.triggered.connect(self._go_gui)
        view_m.addAction(a_gui)

        a_toggle = QAction("Toggle View", self)
        a_toggle.setShortcut("Ctrl+Tab")
        a_toggle.triggered.connect(self._toggle)
        view_m.addAction(a_toggle)

        view_m.addSeparator()

        self._fs_action = QAction("Full Screen", self)
        self._fs_action.setShortcut("F11")
        self._fs_action.setCheckable(True)
        self._fs_action.triggered.connect(self._toggle_fullscreen)
        view_m.addAction(self._fs_action)

        # Tools
        tools_m = mb.addMenu("Tools")

        a_scan = QAction("Scan System for Apps", self)
        a_scan.triggered.connect(self._menu_scan)
        tools_m.addAction(a_scan)

        tools_m.addSeparator()

        theme_sub = tools_m.addMenu("Themes")

        a_t1 = QAction("Dark  —  Black & Green", self)
        a_t1.triggered.connect(lambda: self._set_theme("dark"))
        theme_sub.addAction(a_t1)

        a_t2 = QAction("Dark  —  Black & White", self)
        a_t2.triggered.connect(lambda: self._set_theme("dark_bw"))
        theme_sub.addAction(a_t2)

        a_t3 = QAction("Light  —  Black & White", self)
        a_t3.triggered.connect(lambda: self._set_theme("light"))
        theme_sub.addAction(a_t3)

        a_t4 = QAction("High Contrast", self)
        a_t4.triggered.connect(lambda: self._set_theme("hc"))
        theme_sub.addAction(a_t4)

        a_t5 = QAction("Classic", self)
        a_t5.triggered.connect(lambda: self._set_theme("win95"))
        theme_sub.addAction(a_t5)

        # Help
        help_m = mb.addMenu("Help")

        a_about = QAction("About ST-SoftwareTool", self)
        a_about.triggered.connect(self._about)
        help_m.addAction(a_about)

    # ── central widget ────────────────────────────────────────────────────────

    def _build_central(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.terminal = TerminalWidget(self.manager, self.processor)
        self.terminal.switch_to_gui.connect(self._go_gui)
        self.terminal.exit_app.connect(self.close)
        self.terminal.theme_changed.connect(self._set_theme)
        self.stack.addWidget(self.terminal)   # index 0

        self.gui = GUIView(self.manager)
        self.gui.switch_to_terminal.connect(self._go_terminal)
        self.gui.settings_applied.connect(self._on_settings_applied)
        self.stack.addWidget(self.gui)         # index 1

    # ── switching ─────────────────────────────────────────────────────────────

    def _go_terminal(self):
        self.stack.setCurrentIndex(0)
        self.statusBar().showMessage("Terminal Mode  |  ST-SoftwareTool v1.0")
        self.terminal.focus_input()

    def _go_gui(self):
        self.gui.refresh()
        self.stack.setCurrentIndex(1)
        self.statusBar().showMessage("GUI Mode  |  ST-SoftwareTool v1.0")

    def _toggle(self):
        if self.stack.currentIndex() == 0:
            self._go_gui()
        else:
            self._go_terminal()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._fs_action.setChecked(False)
        else:
            self.showFullScreen()
            self._fs_action.setChecked(True)

    # ── theme ─────────────────────────────────────────────────────────────────

    def _on_settings_applied(self, theme: str, suggestions: bool):
        self._set_theme(theme)
        self.terminal.set_suggestions_enabled(suggestions)

    def _set_theme(self, theme: str):
        self.manager.settings["theme"] = theme
        self.manager.save()
        self.terminal.set_theme(theme)
        self.gui.set_perf_theme(theme)
        self._apply_theme()

    def _apply_theme(self):
        theme = self.manager.settings.get("theme", "dark")

        # Each stylesheet covers every common widget type so the GUI view
        # inherits the correct colours. The terminal widget overrides its own
        # sub-widgets explicitly, so it is unaffected.
        if theme == "dark":
            qss = """
                * { outline: 0; }
                QMainWindow, QDialog, QWidget { background-color: #000000; color: #00ff41; }
                QMenuBar              { background: #0a0a0a; color: #00ff41; border-bottom: 1px solid #1a3a1a; }
                QMenuBar::item:selected { background: #1a3a1a; }
                QMenu                 { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; }
                QMenu::item:selected  { background: #1a3a1a; }
                QStatusBar            { background: #0a0a0a; color: #4a7a4a; font-size: 11px; border-top: 1px solid #1a3a1a; }
                QTabWidget::pane      { background: #000000; border: 1px solid #1a3a1a; }
                QTabBar::tab          { background: #0a0a0a; color: #4a7a4a; border: 1px solid #1a3a1a; padding: 5px 14px; margin-right: 2px; }
                QTabBar::tab:selected { background: #000000; color: #00ff41; border-bottom: 2px solid #00ff41; }
                QTabBar::tab:hover    { color: #00cc33; }
                QGroupBox             { background: #000000; border: 1px solid #1a3a1a; border-radius: 4px; margin-top: 8px; padding-top: 6px; color: #00ff41; font-weight: bold; }
                QGroupBox::title      { subcontrol-origin: margin; left: 8px; color: #00ff41; }
                QListWidget           { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; }
                QListWidget::item:selected { background: #1a3a1a; color: #00ff41; }
                QListWidget::item:hover    { background: #0d1f0d; }
                QTreeWidget           { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; alternate-background-color: #0d0d0d; }
                QTreeWidget::item:selected { background: #1a3a1a; color: #00ff41; }
                QTreeWidget::item:hover    { background: #0d1f0d; }
                QTableView            { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; alternate-background-color: #0d0d0d; gridline-color: #0a0a0a; }
                QTableView::item:selected  { background: #1a3a1a; color: #00ff41; }
                QTableView::item:hover     { background: #0d1f0d; }
                QHeaderView::section  { background: #0d0d0d; color: #4a7a4a; border: 1px solid #1a3a1a; padding: 3px 6px; }
                QTextEdit             { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; }
                QLineEdit             { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; border-radius: 3px; padding: 3px 6px; }
                QLineEdit:focus       { border-color: #00ff41; }
                QLineEdit::placeholder-text { color: #2a5a2a; }
                QPushButton           { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; border-radius: 3px; padding: 4px 12px; }
                QPushButton:hover     { background: #1a3a1a; }
                QPushButton:pressed   { background: #2a4a2a; }
                QPushButton:disabled  { color: #1a2a1a; border-color: #0a1a0a; }
                QComboBox             { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; padding: 3px 6px; }
                QComboBox::drop-down  { border: none; width: 18px; }
                QComboBox QAbstractItemView { background: #0a0a0a; color: #00ff41; border: 1px solid #1a3a1a; selection-background-color: #1a3a1a; }
                QRadioButton          { color: #00ff41; spacing: 6px; background: transparent; }
                QRadioButton::indicator { width: 13px; height: 13px; border: 1px solid #1a3a1a; border-radius: 7px; background: #0a0a0a; }
                QRadioButton::indicator:checked { background: #00ff41; border-color: #00ff41; }
                QCheckBox             { color: #00ff41; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 13px; height: 13px; border: 1px solid #1a3a1a; background: #0a0a0a; }
                QCheckBox::indicator:checked { background: #00ff41; }
                QLabel                { color: #00ff41; background: transparent; }
                QSplitter::handle     { background: #1a3a1a; }
                QScrollBar:vertical   { background: #0a0a0a; width: 8px; border: none; }
                QScrollBar::handle:vertical { background: #1a3a1a; border-radius: 4px; min-height: 20px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QScrollBar:horizontal { background: #0a0a0a; height: 8px; border: none; }
                QScrollBar::handle:horizontal { background: #1a3a1a; border-radius: 4px; }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
                QDialogButtonBox QPushButton { min-width: 70px; }
            """
        elif theme == "dark_bw":
            qss = """
                * { outline: 0; }
                QMainWindow, QDialog, QWidget { background-color: #000000; color: #ffffff; }
                QMenuBar              { background: #0d0d0d; color: #ffffff; border-bottom: 1px solid #333333; }
                QMenuBar::item:selected { background: #333333; }
                QMenu                 { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; }
                QMenu::item:selected  { background: #333333; }
                QStatusBar            { background: #0d0d0d; color: #888888; font-size: 11px; border-top: 1px solid #333333; }
                QTabWidget::pane      { background: #000000; border: 1px solid #333333; }
                QTabBar::tab          { background: #0d0d0d; color: #888888; border: 1px solid #333333; padding: 5px 14px; margin-right: 2px; }
                QTabBar::tab:selected { background: #000000; color: #ffffff; border-bottom: 2px solid #ffffff; }
                QTabBar::tab:hover    { color: #cccccc; }
                QGroupBox             { background: #000000; border: 1px solid #333333; border-radius: 4px; margin-top: 8px; padding-top: 6px; color: #ffffff; font-weight: bold; }
                QGroupBox::title      { subcontrol-origin: margin; left: 8px; color: #ffffff; }
                QListWidget           { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; }
                QListWidget::item:selected { background: #333333; color: #ffffff; }
                QListWidget::item:hover    { background: #1a1a1a; }
                QTreeWidget           { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; alternate-background-color: #111111; }
                QTreeWidget::item:selected { background: #333333; color: #ffffff; }
                QTreeWidget::item:hover    { background: #1a1a1a; }
                QTableView            { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; alternate-background-color: #111111; gridline-color: #0d0d0d; }
                QTableView::item:selected  { background: #333333; color: #ffffff; }
                QTableView::item:hover     { background: #1a1a1a; }
                QHeaderView::section  { background: #111111; color: #888888; border: 1px solid #333333; padding: 3px 6px; }
                QTextEdit             { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; }
                QLineEdit             { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; border-radius: 3px; padding: 3px 6px; }
                QLineEdit:focus       { border-color: #ffffff; }
                QLineEdit::placeholder-text { color: #555555; }
                QPushButton           { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; border-radius: 3px; padding: 4px 12px; }
                QPushButton:hover     { background: #333333; }
                QPushButton:pressed   { background: #444444; }
                QPushButton:disabled  { color: #333333; border-color: #1a1a1a; }
                QComboBox             { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; padding: 3px 6px; }
                QComboBox::drop-down  { border: none; width: 18px; }
                QComboBox QAbstractItemView { background: #0d0d0d; color: #ffffff; border: 1px solid #333333; selection-background-color: #333333; }
                QRadioButton          { color: #ffffff; spacing: 6px; background: transparent; }
                QRadioButton::indicator { width: 13px; height: 13px; border: 1px solid #333333; border-radius: 7px; background: #0d0d0d; }
                QRadioButton::indicator:checked { background: #ffffff; border-color: #ffffff; }
                QCheckBox             { color: #ffffff; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 13px; height: 13px; border: 1px solid #333333; background: #0d0d0d; }
                QCheckBox::indicator:checked { background: #ffffff; }
                QLabel                { color: #ffffff; background: transparent; }
                QSplitter::handle     { background: #333333; }
                QScrollBar:vertical   { background: #0d0d0d; width: 8px; border: none; }
                QScrollBar::handle:vertical { background: #333333; border-radius: 4px; min-height: 20px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QScrollBar:horizontal { background: #0d0d0d; height: 8px; border: none; }
                QScrollBar::handle:horizontal { background: #333333; border-radius: 4px; }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
                QDialogButtonBox QPushButton { min-width: 70px; }
            """
        elif theme == "light":
            qss = """
                * { outline: 0; }
                QMainWindow, QDialog, QWidget { background-color: #c0c0c0; color: #000000; }
                QMenuBar              { background: #b0b0b0; color: #000000; border-bottom: 1px solid #808080; }
                QMenuBar::item:selected { background: #909090; }
                QMenu                 { background: #b8b8b8; color: #000000; border: 1px solid #808080; }
                QMenu::item:selected  { background: #909090; }
                QStatusBar            { background: #b0b0b0; color: #333333; font-size: 11px; border-top: 1px solid #808080; }
                QTabWidget::pane      { background: #c0c0c0; border: 1px solid #808080; }
                QTabBar::tab          { background: #b0b0b0; color: #444444; border: 1px solid #808080; padding: 5px 14px; margin-right: 2px; }
                QTabBar::tab:selected { background: #c0c0c0; color: #000000; border-bottom: 2px solid #000000; }
                QTabBar::tab:hover    { background: #aaaaaa; }
                QGroupBox             { background: #c0c0c0; border: 1px solid #808080; border-radius: 4px; margin-top: 8px; padding-top: 6px; color: #000000; font-weight: bold; }
                QGroupBox::title      { subcontrol-origin: margin; left: 8px; color: #000000; }
                QListWidget           { background: #d0d0d0; color: #000000; border: 1px solid #808080; }
                QListWidget::item:selected { background: #909090; color: #000000; }
                QListWidget::item:hover    { background: #aaaaaa; }
                QTreeWidget           { background: #d0d0d0; color: #000000; border: 1px solid #808080; }
                QTreeWidget::item:selected { background: #909090; color: #000000; }
                QTreeWidget::item:hover    { background: #aaaaaa; }
                QTableView            { background: #d0d0d0; color: #000000; border: 1px solid #808080; alternate-background-color: #c8c8c8; gridline-color: #b0b0b0; }
                QTableView::item:selected  { background: #909090; color: #000000; }
                QTableView::item:hover     { background: #aaaaaa; }
                QHeaderView::section  { background: #b8b8b8; color: #333333; border: 1px solid #808080; padding: 3px 6px; }
                QTextEdit             { background: #d0d0d0; color: #000000; border: 1px solid #808080; }
                QLineEdit             { background: #d0d0d0; color: #000000; border: 1px solid #808080; border-radius: 3px; padding: 3px 6px; }
                QLineEdit:focus       { border-color: #000000; }
                QLineEdit::placeholder-text { color: #666666; }
                QPushButton           { background: #b8b8b8; color: #000000; border: 1px solid #808080; border-radius: 3px; padding: 4px 12px; }
                QPushButton:hover     { background: #a8a8a8; }
                QPushButton:pressed   { background: #989898; }
                QPushButton:disabled  { color: #888888; border-color: #aaaaaa; }
                QComboBox             { background: #d0d0d0; color: #000000; border: 1px solid #808080; padding: 3px 6px; }
                QComboBox::drop-down  { border: none; width: 18px; }
                QComboBox QAbstractItemView { background: #d0d0d0; color: #000000; border: 1px solid #808080; selection-background-color: #909090; }
                QRadioButton          { color: #000000; spacing: 6px; background: transparent; }
                QRadioButton::indicator { width: 13px; height: 13px; border: 1px solid #808080; border-radius: 7px; background: #d0d0d0; }
                QRadioButton::indicator:checked { background: #000000; border-color: #000000; }
                QCheckBox             { color: #000000; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 13px; height: 13px; border: 1px solid #808080; background: #d0d0d0; }
                QCheckBox::indicator:checked { background: #000000; }
                QLabel                { color: #000000; background: transparent; }
                QSplitter::handle     { background: #909090; }
                QScrollBar:vertical   { background: #b8b8b8; width: 8px; border: none; }
                QScrollBar::handle:vertical { background: #888888; border-radius: 4px; min-height: 20px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QScrollBar:horizontal { background: #b8b8b8; height: 8px; border: none; }
                QScrollBar::handle:horizontal { background: #888888; border-radius: 4px; }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
                QDialogButtonBox QPushButton { min-width: 70px; }
            """
        elif theme == "hc":
            qss = """
                * { outline: 0; }
                QMainWindow, QDialog, QWidget { background-color: #000000; color: #ffffff; }
                QMenuBar              { background: #000000; color: #ffff00; border-bottom: 2px solid #ffffff; }
                QMenuBar::item:selected { background: #ffffff; color: #000000; }
                QMenu                 { background: #000000; color: #ffffff; border: 2px solid #ffffff; }
                QMenu::item:selected  { background: #ffffff; color: #000000; }
                QStatusBar            { background: #000000; color: #ffffff; font-size: 11px; border-top: 2px solid #ffffff; }
                QTabWidget::pane      { background: #000000; border: 2px solid #ffffff; }
                QTabBar::tab          { background: #000000; color: #888888; border: 1px solid #ffffff; padding: 5px 14px; margin-right: 2px; }
                QTabBar::tab:selected { background: #000000; color: #ffff00; border-bottom: 3px solid #ffff00; }
                QTabBar::tab:hover    { color: #ffffff; }
                QGroupBox             { background: #000000; border: 2px solid #ffffff; margin-top: 8px; padding-top: 6px; color: #ffff00; font-weight: bold; }
                QGroupBox::title      { subcontrol-origin: margin; left: 8px; color: #ffff00; }
                QListWidget           { background: #000000; color: #ffffff; border: 2px solid #ffffff; }
                QListWidget::item:selected { background: #ffffff; color: #000000; }
                QListWidget::item:hover    { background: #222222; }
                QTreeWidget           { background: #000000; color: #ffffff; border: 2px solid #ffffff; }
                QTreeWidget::item:selected { background: #ffffff; color: #000000; }
                QTreeWidget::item:hover    { background: #222222; }
                QTableView            { background: #000000; color: #ffffff; border: 2px solid #ffffff; alternate-background-color: #111111; gridline-color: #000000; }
                QTableView::item:selected  { background: #ffffff; color: #000000; }
                QTableView::item:hover     { background: #222222; }
                QHeaderView::section  { background: #000000; color: #ffff00; border: 1px solid #ffffff; padding: 3px 6px; }
                QTextEdit             { background: #000000; color: #ffffff; border: 2px solid #ffffff; }
                QLineEdit             { background: #000000; color: #ffffff; border: 2px solid #ffffff; padding: 3px 6px; }
                QLineEdit:focus       { border-color: #ffff00; }
                QPushButton           { background: #000000; color: #ffff00; border: 2px solid #ffffff; padding: 4px 12px; }
                QPushButton:hover     { background: #ffffff; color: #000000; }
                QPushButton:pressed   { background: #ffff00; color: #000000; }
                QPushButton:disabled  { color: #444444; border-color: #444444; }
                QComboBox             { background: #000000; color: #ffffff; border: 2px solid #ffffff; padding: 3px 6px; }
                QComboBox::drop-down  { border: none; width: 18px; }
                QComboBox QAbstractItemView { background: #000000; color: #ffffff; border: 2px solid #ffffff; selection-background-color: #ffffff; selection-color: #000000; }
                QRadioButton          { color: #ffffff; spacing: 6px; background: transparent; }
                QRadioButton::indicator { width: 13px; height: 13px; border: 2px solid #ffffff; border-radius: 7px; background: #000000; }
                QRadioButton::indicator:checked { background: #ffff00; border-color: #ffff00; }
                QCheckBox             { color: #ffffff; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 13px; height: 13px; border: 2px solid #ffffff; background: #000000; }
                QCheckBox::indicator:checked { background: #ffff00; }
                QLabel                { color: #ffffff; background: transparent; }
                QSplitter::handle     { background: #ffffff; width: 2px; height: 2px; }
                QScrollBar:vertical   { background: #000000; width: 10px; border: 1px solid #ffffff; }
                QScrollBar::handle:vertical { background: #ffffff; min-height: 20px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
                QScrollBar:horizontal { background: #000000; height: 10px; border: 1px solid #ffffff; }
                QScrollBar::handle:horizontal { background: #ffffff; }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
                QDialogButtonBox QPushButton { min-width: 70px; }
            """
        elif theme == "win95":
            qss = """
                * { outline: 0; }
                QMainWindow, QDialog, QWidget { background-color: #c0c0c0; color: #000000; }
                QMenuBar              { background: #c0c0c0; color: #000000; border-bottom: 1px solid #808080; }
                QMenuBar::item:selected { background: #000080; color: #ffffff; }
                QMenu                 { background: #c0c0c0; color: #000000;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #000000; border-bottom: 2px solid #000000; }
                QMenu::item:selected  { background: #000080; color: #ffffff; }
                QMenu::separator      { height: 1px; background: #808080; margin: 2px 6px; }
                QStatusBar            { background: #c0c0c0; color: #000000; border-top: 2px solid #808080; font-size: 11px; }
                QTabWidget::pane      { background: #c0c0c0;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QTabBar::tab          { background: #c0c0c0; color: #000000; padding: 4px 12px; margin-right: 2px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: none; }
                QTabBar::tab:selected { background: #c0c0c0; color: #000000; margin-bottom: -1px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #c0c0c0; }
                QTabBar::tab:!selected { margin-top: 2px; background: #b8b8b8; }
                QGroupBox             { background: #c0c0c0; color: #000000; font-weight: bold;
                                        margin-top: 10px; padding-top: 8px;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QGroupBox::title      { subcontrol-origin: margin; left: 8px; background: #c0c0c0; color: #000000; }
                QListWidget           { background: #ffffff; color: #000000;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QListWidget::item:selected { background: #000080; color: #ffffff; }
                QListWidget::item:hover    { background: #dde0ff; }
                QTreeWidget           { background: #ffffff; color: #000000;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QTreeWidget::item:selected { background: #000080; color: #ffffff; }
                QTreeWidget::item:hover    { background: #dde0ff; }
                QTableView            { background: #ffffff; color: #000000;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff;
                                        alternate-background-color: #f0f0f0; gridline-color: #c0c0c0; }
                QTableView::item:selected  { background: #000080; color: #ffffff; }
                QTableView::item:hover     { background: #dde0ff; }
                QHeaderView::section  { background: #c0c0c0; color: #000000; padding: 2px 6px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QTextEdit             { background: #ffffff; color: #000000;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QLineEdit             { background: #ffffff; color: #000000; padding: 2px 4px;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QLineEdit:focus       { border-color: #000080; }
                QLineEdit::placeholder-text { color: #808080; }
                QPushButton           { background: #c0c0c0; color: #000000; padding: 3px 10px; min-width: 60px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QPushButton:hover     { background: #d0d0d0; }
                QPushButton:pressed   { border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff;
                                        padding-top: 4px; padding-left: 12px; }
                QPushButton:disabled  { color: #808080; }
                QComboBox             { background: #ffffff; color: #000000; padding: 2px 4px;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QComboBox::drop-down  { background: #c0c0c0; width: 18px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QComboBox QAbstractItemView { background: #ffffff; color: #000000;
                                             border: 2px solid #808080;
                                             selection-background-color: #000080; selection-color: #ffffff; }
                QRadioButton          { color: #000000; spacing: 6px; background: transparent; }
                QRadioButton::indicator { width: 13px; height: 13px; border: 2px solid #808080; border-radius: 7px; background: #ffffff; }
                QRadioButton::indicator:checked { background: #000000; border-color: #808080; }
                QCheckBox             { color: #000000; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 13px; height: 13px; background: #ffffff;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QCheckBox::indicator:checked { background: #000000; }
                QLabel                { color: #000000; background: transparent; }
                QSplitter::handle     { background: #c0c0c0; border: 1px solid #808080; width: 4px; height: 4px; }
                QScrollBar:vertical   { background: #c0c0c0; width: 16px; border: none; }
                QScrollBar::handle:vertical { background: #c0c0c0; min-height: 20px;
                                             border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                             border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 16px; background: #c0c0c0;
                    border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                    border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #b0b0b0; }
                QScrollBar:horizontal { background: #c0c0c0; height: 16px; border: none; }
                QScrollBar::handle:horizontal { background: #c0c0c0;
                                               border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                               border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 16px; background: #c0c0c0;
                    border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                    border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: #b0b0b0; }
                QDialogButtonBox QPushButton { min-width: 70px; }
            """

        self.setStyleSheet(qss)

    def closeEvent(self, event):
        self.gui.cleanup()
        super().closeEvent(event)

    # ── menu actions ──────────────────────────────────────────────────────────

    def _menu_add_app(self):
        self._go_gui()
        self.gui.tabs.setCurrentIndex(0)
        self.gui._add_app()

    def _menu_scan(self):
        self._go_gui()
        self.gui._scan()

    def _about(self):
        _SHORT = (
            "Built exclusively for Windows, ST-SoftwareTool is a "
            "professional management utility."
        )
        _FULL = (
            "Built exclusively for Windows, ST-SoftwareTool is a professional "
            "management utility. Monitor active hardware via our advanced terminal "
            "or an intuitive GUI. Protect your privacy by blocking unwanted "
            "background app installations. Take complete control of your PC system "
            "and easily uncover hidden files. Secure your data and experience "
            "full control now."
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("About ST-SoftwareTool")
        dlg.setMinimumWidth(460)

        root = QVBoxLayout(dlg)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 12)

        # Header row: icon + title
        hdr = QHBoxLayout()
        _icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  'assets', 'STsoftwareterminalLOGO.png.png')
        if os.path.exists(_icon_path):
            ico = QLabel()
            ico.setPixmap(QPixmap(_icon_path).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            hdr.addWidget(ico)
            hdr.addSpacing(10)
        title = QLabel("<b>ST-SoftwareTool  v1.0</b>")
        title.setFont(QFont("Segoe UI", 13))
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)

        # Short description
        short_lbl = QLabel(_SHORT)
        short_lbl.setWordWrap(True)
        root.addWidget(short_lbl)

        # Full description (hidden by default)
        full_lbl = QLabel(_FULL)
        full_lbl.setWordWrap(True)
        full_lbl.setVisible(False)
        root.addWidget(full_lbl)

        # Toggle button
        toggle_btn = QPushButton("See full description")
        toggle_btn.setFixedWidth(160)

        def _toggle():
            vis = not full_lbl.isVisible()
            full_lbl.setVisible(vis)
            toggle_btn.setText("Hide description" if vis else "See full description")
            dlg.adjustSize()

        toggle_btn.clicked.connect(_toggle)
        root.addWidget(toggle_btn)

        # Keyboard shortcuts
        shortcuts = QLabel(
            "<b>Keyboard shortcuts:</b><br>"
            "Ctrl+1  —  Terminal mode<br>"
            "Ctrl+2  —  GUI mode<br>"
            "Ctrl+Tab  —  Toggle mode<br>"
            "Ctrl+N  —  Add app (GUI)<br>"
            "&#8593; / &#8595;  —  Command history (terminal)"
        )
        shortcuts.setTextFormat(Qt.RichText)
        root.addWidget(shortcuts)

        # OK button
        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        root.addWidget(btns)

        dlg.exec()
