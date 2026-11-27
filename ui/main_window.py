import os
import time
from PySide6.QtWidgets import (
    QMainWindow, QStackedWidget, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialogButtonBox,
    QSystemTrayIcon, QMenu, QApplication,
)
from PySide6.QtGui import QAction, QKeySequence, QIcon, QFont, QPixmap, QCursor
from PySide6.QtCore import Qt, QTimer
from core import mac_appkit
from . import glass
from .terminal_widget import TerminalWidget
from .gui_view import GUIView
from core.privacy_monitor import PrivacyMonitor
from core.background_tasks import BackgroundScanner
from core.updater import APP_VERSION, check_for_update
from core.osinfo import IS_MAC


class MainWindow(QMainWindow):
    def __init__(self, manager, processor):
        super().__init__()
        self.manager      = manager
        self.processor    = processor
        self._force_quit  = False   # set True to bypass minimize-to-tray
        self.setWindowTitle(f"ST-SoftwareTool  v{APP_VERSION}")
        self.setMinimumSize(820, 580)
        self.resize(960, 660)

        _icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "STsoftwareterminalLOGO.png.png")
        self._app_icon = QIcon(_icon) if os.path.exists(_icon) else QIcon()
        self.setWindowIcon(self._app_icon)

        # macOS Glass theme: the window must be translucent before its native window
        # exists. Opaque themes paint a solid background, so they look the same.
        self._backdrop = glass.MacBackdrop(self)
        self._glass_native = glass.native_blur_supported()
        if self._glass_native:
            self.setAttribute(Qt.WA_TranslucentBackground)
        try:
            QApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)
        except AttributeError:
            pass

        # Instantiate scanner/privacy before building menu (menu references them)
        self._privacy = PrivacyMonitor(self)
        self._scanner = BackgroundScanner(self)
        self._perf_widget = None   # macOS desktop Performance widget, when shown

        self._build_menu()
        self._build_central()
        self._build_tray()
        self._apply_theme()

        # Wire up privacy monitor
        self._privacy.camera_started.connect(self._on_camera_on)
        self._privacy.camera_stopped.connect(self._on_camera_off)
        self._privacy.mic_started.connect(self._on_mic_on)
        self._privacy.mic_stopped.connect(self._on_mic_off)
        self._privacy.set_enabled(
            self.manager.settings.get('monitor_privacy', True)
        )
        self._privacy.start()

        # Wire up hourly background scanner
        self._scanner.scan_started.connect(self._on_auto_scan_started)
        self._scanner.scan_done.connect(self._on_auto_scan_done)
        self._scanner.threat_blocked.connect(self._on_auto_blocked)
        self._scanner.set_auto_block(
            self.manager.settings.get('auto_block_threats', True)
        )
        if self.manager.settings.get('hourly_scan', True):
            self._scanner.start()

        # VPN auto-connect (delayed so the window has time to finish painting)
        if manager.settings.get('auto_vpn_startup', False):
            QTimer.singleShot(2500, self._auto_vpn_connect)

        if manager.settings.get("default_view", "terminal") == "gui":
            self._go_gui()
        else:
            self._go_terminal()

        # Bring back the desktop widget if it was on when the app last ran
        if IS_MAC and manager.settings.get('perf_widget', False):
            QTimer.singleShot(800, lambda: self._set_perf_widget(True))

        if IS_MAC:
            # Reopening the app (Dock, Launchpad, Finder) shows the window again
            QApplication.instance().applicationStateChanged.connect(self._on_app_state)
            self._reopen_block_until = time.monotonic() + 3.0   # ignore the launch itself
            # Rounded light / dark app icon (Settings › App Icon); Automatic follows the Mac
            self._apply_app_icon()
            QApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: self._apply_app_icon(only_auto=True))

    # ── system tray ───────────────────────────────────────────────────────────

    def _build_tray(self):
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("ST-SoftwareTool")
        self._popup = None

        if IS_MAC:
            # macOS: the ST logo in the menu bar opens a Stats-style popup instead of a menu
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.setVisible(bool(self.manager.settings.get('menubar_icon', True)))
            self._popup_timer = QTimer(self)
            self._popup_timer.setInterval(1000)
            self._popup_timer.timeout.connect(self._refresh_popup)
            return

        menu = QMenu()
        a_open  = QAction("Open ST-SoftwareTool", self)
        a_scan  = QAction("Quick Scan Now", self)
        a_exit  = QAction("Exit", self)
        a_open.triggered.connect(self._tray_open)
        a_scan.triggered.connect(self._scanner.trigger_now)
        a_exit.triggered.connect(self._quit_app)
        menu.addAction(a_open)
        menu.addSeparator()
        menu.addAction(a_scan)
        menu.addSeparator()
        menu.addAction(a_exit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _tray_open(self):
        if IS_MAC:
            mac_appkit.set_activation_policy(mac_appkit.REGULAR)   # Dock icon back
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if IS_MAC:
            mac_appkit.activate_app()

    def _on_tray_activated(self, reason):
        if IS_MAC:
            self._toggle_popup()
            return
        if reason == QSystemTrayIcon.DoubleClick:
            self._tray_open()

    # ── macOS menu-bar popup ──────────────────────────────────────────────────

    def _get_popup(self):
        if self._popup is None:
            from ui.menubar_popup import MenuBarPopup
            p = MenuBarPopup(APP_VERSION)
            p.open_app.connect(self._tray_open)
            p.quit_app.connect(self._quit_app)
            p.widget_toggled.connect(self._set_perf_widget)
            p.vpn_clicked.connect(self._popup_vpn_clicked)
            self.gui._perf_worker.perf_ready.connect(p.panel.set_perf)
            self.gui._gpu_worker.gpu_ready.connect(p.panel.set_gpu)
            self._seed_perf(p.panel)
            self._popup = p
        return self._popup

    def _toggle_popup(self):
        popup = self._get_popup()
        if popup.isVisible():
            popup.hide()
            return
        if time.monotonic() - popup.closed_at < 0.3:
            return   # this click on the icon is what just closed it
        self._refresh_popup()
        popup.show_below(self._tray.geometry())
        self._popup_timer.start()

    def _refresh_popup(self):
        popup = self._popup
        if popup is None or not popup.isVisible():
            if hasattr(self, '_popup_timer'):
                self._popup_timer.stop()
            if popup is None:
                return
        popup.set_widget_checked(bool(self.manager.settings.get('perf_widget', False)))
        view = getattr(self.gui, '_vpn_view', None)
        if view is None:
            popup.set_vpn(False)
        else:
            popup.set_vpn(True, view.is_connected(), view.is_busy())

    def _popup_vpn_clicked(self):
        view = getattr(self.gui, '_vpn_view', None)
        if view is not None:
            view.toggle_from_menu_bar()
            self._refresh_popup()

    def _set_menubar_icon(self, on: bool):
        self.manager.settings['menubar_icon'] = bool(on)
        self._save_settings_quietly()
        self._tray.setVisible(bool(on))
        if not on and self._popup is not None:
            self._popup.hide()

    # ── macOS: running in the menu bar while the window is closed ────────────

    def _keeps_running_when_closed(self) -> bool:
        s = self.manager.settings
        if s.get('run_in_background', False):
            return True
        # macOS: like Stats, closing the window leaves the app in the menu bar (and keeps the
        # desktop widget); ⌘Q or Quit in the popup really quits.
        return IS_MAC and (s.get('menubar_icon', True) or s.get('perf_widget', False))

    def enter_background(self):
        """Hide the window and (macOS) the Dock icon; keep running in the menu bar."""
        self.hide()
        if IS_MAC:
            mac_appkit.set_activation_policy(mac_appkit.ACCESSORY)

    def _on_app_state(self, state):
        if state == Qt.ApplicationActive:
            # the Mac's icon style may have changed while ST was in the background
            self._apply_app_icon(only_auto=True)
        # Opening the app again (Dock, Launchpad, Finder) while only the menu bar part runs
        # makes macOS re-activate this instance: show the window again.
        if state == Qt.ApplicationActive and self.isHidden():
            QTimer.singleShot(250, self._maybe_reopen)

    def _apply_app_icon(self, only_auto: bool = False):
        """macOS: show the light or dark rounded icon chosen in Settings › App Icon."""
        if not IS_MAC:
            return
        if only_auto and self.manager.settings.get('app_icon', 'auto') != 'auto':
            return
        from ui import app_icon
        before = self.manager.settings.get('app_icon_bundle')
        self._icon_style = app_icon.apply(self.manager.settings)
        if self.manager.settings.get('app_icon_bundle') != before:
            self._save_settings_quietly()

    def _maybe_reopen(self):
        if not self.isHidden() or time.monotonic() < self._reopen_block_until:
            return
        popup = self._popup
        if popup is not None and (popup.isVisible() or time.monotonic() - popup.closed_at < 1.0):
            return   # activated by the menu-bar popup
        if QApplication.activePopupWidget() is not None:
            return   # e.g. the widget's right-click menu
        w = self._perf_widget
        if w is not None and w.frameGeometry().contains(QCursor.pos()):
            return   # a click on the desktop widget
        self._tray_open()


    def _notify(self, title: str, msg: str,
                icon=QSystemTrayIcon.Information, ms: int = 5000):
        if self._tray.isSystemTrayAvailable():
            self._tray.showMessage(title, msg, icon, ms)

    # ── privacy monitor callbacks ─────────────────────────────────────────────

    def _on_camera_on(self, apps: str):
        self._notify(
            "ST-SoftwareTool  —  Camera Alert",
            f"Camera is now ON\nUsed by: {apps}",
            QSystemTrayIcon.Warning, 6000,
        )

    def _on_camera_off(self):
        self._notify(
            "ST-SoftwareTool  —  Camera",
            "Camera turned OFF",
            QSystemTrayIcon.Information, 3000,
        )

    def _on_mic_on(self, apps: str):
        self._notify(
            "ST-SoftwareTool  —  Microphone Alert",
            f"Microphone is now ON\nUsed by: {apps}",
            QSystemTrayIcon.Warning, 6000,
        )

    def _on_mic_off(self):
        self._notify(
            "ST-SoftwareTool  —  Microphone",
            "Microphone turned OFF",
            QSystemTrayIcon.Information, 3000,
        )

    # ── background scanner callbacks ──────────────────────────────────────────

    def _auto_vpn_connect(self):
        try:
            if self.gui._vpn_view is not None:
                self.gui._vpn_view.request_auto_connect()
        except Exception:
            pass

    def _on_auto_scan_started(self):
        self._notify(
            "ST-AntiVirus",
            "Automatic hourly scan started…",
            QSystemTrayIcon.Information, 3000,
        )

    def _on_auto_scan_done(self, total: int, blocked: int):
        if total == 0:
            self._notify(
                "ST-AntiVirus  —  All Clear",
                "Hourly scan finished.  No threats found.  Your system is clean.",
                QSystemTrayIcon.Information, 6000,
            )
        elif blocked > 0:
            self._notify(
                "ST-AntiVirus  —  Threats Blocked!",
                f"Scan complete: {total} threat(s) found.\n"
                f"{blocked} critical threat(s) were automatically blocked.",
                QSystemTrayIcon.Critical, 8000,
            )
            QMessageBox.warning(
                self,
                "ST-AntiVirus — Threats Blocked",
                f"Hourly scan complete.\n\n"
                f"{total} threat(s) were detected.\n"
                f"{blocked} critical threat(s) were automatically blocked.\n\n"
                f"Open the AntiVirus tab to review all findings.",
            )
        else:
            self._notify(
                "ST-AntiVirus  —  Threats Detected",
                f"Scan complete: {total} threat(s) found.\n"
                f"Open ST-AntiVirus to review and block them.",
                QSystemTrayIcon.Warning, 8000,
            )
            QMessageBox.warning(
                self,
                "ST-AntiVirus — Threats Detected",
                f"Hourly scan complete.\n\n"
                f"{total} threat(s) were detected on your system.\n\n"
                f"Open the AntiVirus tab to review and block them.",
            )

    def _on_auto_blocked(self, threat):
        self._notify(
            "ST-AntiVirus  —  Threat Blocked",
            f"Automatically blocked: {threat.category}\n{threat.path}",
            QSystemTrayIcon.Critical, 5000,
        )

    # ── settings changed (from gui) ───────────────────────────────────────────

    def _on_settings_changed(self):
        s = self.manager.settings
        self._apply_app_icon()
        self._privacy.set_enabled(s.get('monitor_privacy', True))
        self._scanner.set_auto_block(s.get('auto_block_threats', True))
        if s.get('hourly_scan', True):
            self._scanner.start()
        else:
            self._scanner.stop()

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
        a_exit.triggered.connect(self._quit_app)   # always fully quit
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

        if IS_MAC:
            view_m.addSeparator()
            self._widget_action = QAction("Performance Widget on Desktop", self)
            self._widget_action.setCheckable(True)
            self._widget_action.setChecked(bool(self.manager.settings.get('perf_widget', False)))
            self._widget_action.triggered.connect(self._set_perf_widget)
            view_m.addAction(self._widget_action)

            self._menubar_action = QAction("Show ST-SoftwareTool in Menu Bar", self)
            self._menubar_action.setCheckable(True)
            self._menubar_action.setChecked(bool(self.manager.settings.get('menubar_icon', True)))
            self._menubar_action.triggered.connect(self._set_menubar_icon)
            view_m.addAction(self._menubar_action)

        # Tools
        tools_m = mb.addMenu("Tools")

        a_scan = QAction("Scan System for Apps", self)
        a_scan.triggered.connect(self._menu_scan)
        tools_m.addAction(a_scan)

        a_av_scan = QAction("Quick AntiVirus Scan", self)
        a_av_scan.triggered.connect(self._scanner.trigger_now)
        tools_m.addAction(a_av_scan)

        tools_m.addSeparator()

        theme_sub = tools_m.addMenu("Themes")
        for label, key in (
            ("Dark  —  Black && Green", "dark"),
            ("Dark  —  Black && White", "dark_bw"),
            ("Light  —  Black && White", "light"),
            ("High Contrast", "hc"),
            ("Classic", "win95"),
            ("macOS Glass", "glass"),
        ):
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            theme_sub.addAction(a)

        # Help
        help_m = mb.addMenu("Help")

        a_update = QAction("Check for Updates", self)
        a_update.triggered.connect(lambda: check_for_update(self, force=True))
        help_m.addAction(a_update)

        help_m.addSeparator()

        a_about = QAction("About ST-SoftwareTool", self)
        a_about.triggered.connect(self._about)
        help_m.addAction(a_about)

    # ── central widget ────────────────────────────────────────────────────────

    def _build_central(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.terminal = TerminalWidget(self.manager, self.processor)
        self.terminal.switch_to_gui.connect(self._go_gui)
        self.terminal.exit_app.connect(self._quit_app)
        self.terminal.theme_changed.connect(self._set_theme)
        self.stack.addWidget(self.terminal)   # index 0

        self.gui = GUIView(self.manager)
        self.gui.switch_to_terminal.connect(self._go_terminal)
        self.gui.settings_applied.connect(self._on_settings_applied)
        self.gui.settings_changed.connect(self._on_settings_changed)
        self.gui.perf_widget_toggled.connect(self._set_perf_widget)
        self.gui.glass_changed.connect(self._on_glass_changed)
        self.stack.addWidget(self.gui)         # index 1

    # ── switching ─────────────────────────────────────────────────────────────

    def _go_terminal(self):
        self.stack.setCurrentIndex(0)
        self.statusBar().showMessage(f"Terminal Mode  |  ST-SoftwareTool v{APP_VERSION}")
        self.terminal.focus_input()

    def _go_gui(self):
        self.gui.refresh()
        self.stack.setCurrentIndex(1)
        self.statusBar().showMessage(f"GUI Mode  |  ST-SoftwareTool v{APP_VERSION}")

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
        try:
            self.manager.save()
        except Exception:
            pass
        self.terminal.set_theme(theme)
        self.gui.set_perf_theme(theme)
        self._apply_theme()

    def _on_glass_changed(self):
        """Gloss / blur slider moved in Settings — restyle live if glass is active."""
        if self.manager.settings.get("theme") == glass.THEME_KEY:
            self._apply_theme()

    def _on_color_scheme_changed(self, *_):
        # The glass theme follows macOS Light / Dark mode.
        if self.manager.settings.get("theme") == glass.THEME_KEY:
            self._apply_theme()
            self.terminal.set_theme(glass.THEME_KEY, keep_output=True)
            self.gui.set_perf_theme(glass.THEME_KEY)

    def showEvent(self, event):
        super().showEvent(event)
        if self.manager.settings.get("theme") == glass.THEME_KEY and not self._backdrop.attached:
            self._apply_theme()   # the native backdrop needs an on-screen window

    def _apply_theme(self):
        theme = self.manager.settings.get("theme", "win95")

        if theme == glass.THEME_KEY:
            gloss, blur = glass.settings_values(self.manager.settings)
            dark = glass.is_dark()
            native = self._backdrop.apply(True, blur, dark) if self.isVisible() else self._glass_native
            self.setStyleSheet(glass.build_qss(gloss, blur, native, dark))
            return
        self._backdrop.apply(False, 0, True)

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
                QRadioButton::indicator { width: 13px; height: 13px; border: 2px solid #00ff41; border-radius: 7px; background: #0a0a0a; }
                QRadioButton::indicator:checked { background: #00ff41; border-color: #00ff41; }
                QCheckBox             { color: #00ff41; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 14px; height: 14px; border: 2px solid #00ff41; background: #0a0a0a; }
                QCheckBox::indicator:checked { background: #00ff41; border: 2px solid #00cc33; }
                QCheckBox::indicator:disabled { border-color: #1a3a1a; }
                QLabel                { color: #00ff41; background: transparent; }
                QSlider::groove:horizontal { height: 4px; background: #1a3a1a; border-radius: 2px; margin: 5px 0; }
                QSlider::handle:horizontal { background: #00ff41; border: 1px solid #00cc33; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0; }
                QSlider::sub-page:horizontal { background: #00ff41; border-radius: 2px; }
                QSlider::add-page:horizontal { background: #1a3a1a; border-radius: 2px; }
                QSlider::groove:vertical { width: 4px; background: #1a3a1a; border-radius: 2px; margin: 0 5px; }
                QSlider::handle:vertical { background: #00ff41; border: 1px solid #00cc33; width: 14px; height: 14px; border-radius: 7px; margin: 0 -5px; }
                QSlider:disabled { opacity: 0.35; }
                QProgressBar          { background: #0a0a0a; border: 1px solid #1a3a1a; color: #00ff41; text-align: center; border-radius: 2px; }
                QProgressBar::chunk   { background: #00ff41; border-radius: 2px; }
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
                QRadioButton::indicator { width: 13px; height: 13px; border: 2px solid #888888; border-radius: 7px; background: #0d0d0d; }
                QRadioButton::indicator:checked { background: #ffffff; border-color: #ffffff; }
                QCheckBox             { color: #ffffff; spacing: 6px; background: transparent; }
                QCheckBox::indicator  { width: 14px; height: 14px; border: 2px solid #888888; background: #0d0d0d; }
                QCheckBox::indicator:checked { background: #ffffff; border: 2px solid #aaaaaa; }
                QCheckBox::indicator:disabled { border-color: #333333; }
                QLabel                { color: #ffffff; background: transparent; }
                QSlider::groove:horizontal { height: 4px; background: #333333; border-radius: 2px; margin: 5px 0; }
                QSlider::handle:horizontal { background: #888888; border: 1px solid #ffffff; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0; }
                QSlider::sub-page:horizontal { background: #ffffff; border-radius: 2px; }
                QSlider::add-page:horizontal { background: #333333; border-radius: 2px; }
                QSlider::groove:vertical { width: 4px; background: #333333; border-radius: 2px; margin: 0 5px; }
                QSlider::handle:vertical { background: #888888; border: 1px solid #ffffff; width: 14px; height: 14px; border-radius: 7px; margin: 0 -5px; }
                QSlider:disabled { opacity: 0.35; }
                QProgressBar          { background: #0d0d0d; border: 1px solid #333333; color: #ffffff; text-align: center; border-radius: 2px; }
                QProgressBar::chunk   { background: #888888; border-radius: 2px; }
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
                QCheckBox::indicator  { width: 14px; height: 14px; border: 2px solid #ffffff; background: #000000; }
                QCheckBox::indicator:checked { background: #ffff00; border: 2px solid #ffff00; }
                QCheckBox::indicator:disabled { border-color: #444444; }
                QLabel                { color: #ffffff; background: transparent; }
                QSlider::groove:horizontal { height: 4px; background: #444444; border: 1px solid #ffffff; border-radius: 0; margin: 6px 0; }
                QSlider::handle:horizontal { background: #ffff00; border: 2px solid #ffffff; width: 16px; height: 16px; border-radius: 0; margin: -7px 0; }
                QSlider::sub-page:horizontal { background: #ffffff; }
                QSlider::add-page:horizontal { background: #000000; border: 1px solid #ffffff; }
                QSlider::groove:vertical { width: 4px; background: #444444; border: 1px solid #ffffff; margin: 0 6px; }
                QSlider::handle:vertical { background: #ffff00; border: 2px solid #ffffff; width: 16px; height: 16px; border-radius: 0; margin: 0 -7px; }
                QSlider:disabled { opacity: 0.35; }
                QProgressBar          { background: #000000; border: 2px solid #ffffff; color: #ffffff; text-align: center; }
                QProgressBar::chunk   { background: #ffff00; }
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
                QPushButton           { background: #c0c0c0; color: #000000; padding: 3px 10px; min-width: 60px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QPushButton:hover     { background: #d0d0d0; }
                QPushButton:pressed   { border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QPushButton:disabled  { color: #808080; }
                QComboBox             { background: #ffffff; color: #000000; padding: 2px 4px;
                                        border-top: 2px solid #808080; border-left: 2px solid #808080;
                                        border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }
                QComboBox::drop-down  { background: #c0c0c0; width: 18px;
                                        border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
                                        border-right: 2px solid #808080; border-bottom: 2px solid #808080; }
                QComboBox QAbstractItemView { background: #ffffff; color: #000000; border: 2px solid #808080;
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
        else:
            qss = ""

        self.setStyleSheet(qss)

    # ── close / quit ──────────────────────────────────────────────────────────

    def _quit_app(self):
        """Unconditionally quit — bypasses minimize-to-tray."""
        self._force_quit = True
        self.close()

    def closeEvent(self, event):
        # macOS: the red close button sends a spontaneous close; ⌘Q, Dock › Quit and logout
        # ask every window to close programmatically — those must really quit.
        quitting = IS_MAC and not event.spontaneous()
        if not self._force_quit and not quitting and self._keeps_running_when_closed():
            event.ignore()
            self.enter_background()
            if not IS_MAC:
                self._notify(
                    "ST-SoftwareTool",
                    "Running in the background.\n"
                    "Right-click the tray icon to open or exit.",
                    QSystemTrayIcon.Information, 4000,
                )
            elif not self.manager.settings.get('menubar_hint_shown', False):
                self.manager.settings['menubar_hint_shown'] = True
                self._save_settings_quietly()
                self._notify(
                    "ST-SoftwareTool is still running",
                    "It stays in the menu bar (and keeps the desktop widget). Click the ST logo "
                    "at the top for quick stats, or press ⌘Q to quit.",
                    QSystemTrayIcon.Information, 6000,
                )
        else:
            self._do_cleanup()
            event.accept()

    # ── desktop Performance widget (macOS) ───────────────────────────────────

    def _set_perf_widget(self, on: bool):
        """Show or remove the desktop Performance widget and remember the choice."""
        on = bool(on)
        if self.manager.settings.get('perf_widget', False) != on:
            self.manager.settings['perf_widget'] = on
            self._save_settings_quietly()
        if hasattr(self, '_widget_action'):
            self._widget_action.setChecked(on)
        self.gui.set_perf_widget_checked(on)
        if self._popup is not None:
            self._popup.set_widget_checked(on)
        if IS_MAC and self.manager.settings.get('start_with_windows', False):
            # At login, start quietly in the menu bar when the widget is on (no window)
            GUIView._set_startup_mac(True, background=on)

        if on and self._perf_widget is None:
            from ui.perf_widget import PerfWidget
            w = PerfWidget()
            self.gui._perf_worker.perf_ready.connect(w.set_perf)
            self.gui._gpu_worker.gpu_ready.connect(w.set_gpu)
            w.open_requested.connect(self._tray_open)
            w.remove_requested.connect(lambda: self._set_perf_widget(False))
            w.moved.connect(self._on_perf_widget_moved)
            self._seed_perf(w)
            w.place(self.manager.settings.get('perf_widget_pos'))
            self._perf_widget = w
            w.show()
        elif not on and self._perf_widget is not None:
            self._perf_widget.dispose()
            self._perf_widget.deleteLater()
            self._perf_widget = None

    def _seed_perf(self, panel):
        """Show the latest numbers immediately instead of '—' until the next sample."""
        if self.gui.last_perf is not None:
            panel.set_perf(self.gui.last_perf)
        panel.set_gpu(self.gui.last_gpu)

    def _on_perf_widget_moved(self, x: int, y: int):
        self.manager.settings['perf_widget_pos'] = [x, y]
        self._save_settings_quietly()

    def _save_settings_quietly(self):
        try:
            self.manager.save()
        except Exception:
            pass

    def _do_cleanup(self):
        if self._perf_widget is not None:
            self._perf_widget.dispose()
            self._perf_widget = None
        if self._popup is not None:
            self._popup.hide()
        self.gui.cleanup()
        self._scanner.stop()
        self._privacy.shutdown()
        self._tray.hide()

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
            "Built for Windows and macOS, ST-SoftwareTool is a "
            "professional management utility."
        )
        _FULL = (
            "Built for Windows and macOS, ST-SoftwareTool is a professional "
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

        hdr = QHBoxLayout()
        _icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  'assets', 'STsoftwareterminalLOGO.png.png')
        if os.path.exists(_icon_path):
            ico = QLabel()
            ico.setPixmap(QPixmap(_icon_path).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            hdr.addWidget(ico)
            hdr.addSpacing(10)
        title = QLabel(f"<b>ST-SoftwareTool  v{APP_VERSION}</b>")
        title.setFont(QFont("Segoe UI", 13))
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)

        short_lbl = QLabel(_SHORT)
        short_lbl.setWordWrap(True)
        root.addWidget(short_lbl)

        full_lbl = QLabel(_FULL)
        full_lbl.setWordWrap(True)
        full_lbl.setVisible(False)
        root.addWidget(full_lbl)

        toggle_btn = QPushButton("See full description")
        toggle_btn.setFixedWidth(160)

        def _toggle():
            vis = not full_lbl.isVisible()
            full_lbl.setVisible(vis)
            toggle_btn.setText("Hide description" if vis else "See full description")
            dlg.adjustSize()

        toggle_btn.clicked.connect(_toggle)
        root.addWidget(toggle_btn)

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

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        root.addWidget(btns)

        dlg.exec()
