import sys

# "<app> --proc-monitor": run only the Task Manager's process collector (the macOS build
# has no separate proc_monitor binary). Handled before any Qt import so no window, Dock
# icon or UI code is involved.
if __name__ == "__main__" and "--proc-monitor" in sys.argv[1:]:
    from core.proc_monitor import main as _proc_monitor_main
    _proc_monitor_main()
    sys.exit(0)

import os, ctypes


def _use_bundled_certificates() -> None:
    """macOS: Python's OpenSSL does not read the macOS keychain, and the certificate file it
    was built to look for doesn't exist on users' Macs, so every HTTPS request (update check,
    antivirus lookups) failed with CERTIFICATE_VERIFY_FAILED. Point OpenSSL at the certifi
    bundle that ships inside the app. Windows reads its own certificate store and needs nothing."""
    if sys.platform != "darwin" or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        path = certifi.where()
    except Exception:
        return
    if os.path.isfile(path):
        os.environ["SSL_CERT_FILE"] = path   # read by OpenSSL whenever a context is created


_use_bundled_certificates()   # before anything opens a network connection

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon
from PySide6.QtCore import QTimer
from core.manager import AppManager
from core.commands import CommandProcessor
from core.updater import start_auto_updates
from ui.main_window import MainWindow
from core.osinfo import IS_WINDOWS


def _set_process_icon(icon_path: str) -> None:
    """Set the taskbar / title-bar icon for this process via Windows API."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('STSoftwareTool.v1')
    except Exception:
        pass
    try:
        # Load the PNG as a Windows HICON and stamp it on the console window
        # (if any) and the hidden Qt message window so it shows immediately.
        LR_LOADFROMFILE = 0x0010
        hicon = ctypes.windll.user32.LoadImageW(
            None, icon_path, 1,  # IMAGE_ICON=1
            0, 0, LR_LOADFROMFILE,
        )
        if hicon:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                WM_SETICON = 0x0080
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, hicon)  # ICON_BIG
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, hicon)  # ICON_SMALL
    except Exception:
        pass


def _smoke_test(app, window, seconds: float) -> None:
    """ST_SMOKE_TEST=<seconds>: open every tab, then quit. Exit code 1 if anything raised.
    Used by the build pipeline to prove the packaged app really starts on each OS."""
    import traceback
    errors = []

    def hook(etype, value, tb):
        errors.append(''.join(traceback.format_exception(etype, value, tb)))
        sys.__excepthook__(etype, value, tb)
    sys.excepthook = hook

    def visit_tabs():
        from PySide6.QtGui import QFontInfo
        from core.osinfo import mac_app_bundle, own_executable
        print(f"SMOKE executable={sys.executable!r} own={own_executable()!r} "
              f"bundle={mac_app_bundle()!r} task_cmd={window.gui._task_backend._cmd!r}",
              flush=True)
        if os.environ.get("ST_SMOKE_TOR"):
            # The VPN's Tor: found, prepared like a connect does, and able to run
            import subprocess
            from core import vpn_manager
            tor = vpn_manager.TorVpnManager().find_tor()
            version = ''
            if tor:
                if sys.platform == "darwin":
                    vpn_manager._prepare_bundled_tor(tor)
                try:
                    version = subprocess.run([tor, '--version'], capture_output=True, text=True,
                                             timeout=20).stdout.strip().splitlines()[0]
                except Exception as exc:
                    version = f'failed: {exc}'
            print(f"SMOKE tor={tor!r} version={version!r}", flush=True)
            if not tor or not version.startswith('Tor version'):
                errors.append(f"VPN: Tor not found or not runnable ({tor}, {version})")
        if os.environ.get("ST_SMOKE_HTTPS"):
            # The real update check, over HTTPS, as the user's app does it
            from core import updater
            print(f"SMOKE ssl cert file={os.environ.get('SSL_CERT_FILE')!r}", flush=True)
            result, _asset, detail = updater._fetch_latest()
            print(f"SMOKE update check -> {result!r} {detail!r}", flush=True)
            if result == updater._ERROR:
                errors.append(f"Update check failed: {detail}")
        for family in ("Consolas", "Segoe UI"):
            info = QFontInfo(QFont(family, 9))
            print(f"SMOKE font '{family}' -> '{info.family()}' fixedPitch={info.fixedPitch()}",
                  flush=True)
        tabs = window.gui.tabs
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            app.processEvents()
            print(f"SMOKE tab {i}: {tabs.tabText(i)}", flush=True)

    def finish():
        print(f"SMOKE {'FAILED' if errors else 'OK'} ({len(errors)} error(s))", flush=True)
        for e in errors:
            print(e, flush=True)
        window._force_quit = True
        window.gui.cleanup()
        os._exit(1 if errors else 0)

    # ST_SMOKE_SHOTS=<folder>: afterwards show each tab for a few seconds (so lists that
    # load in the background can fill) and save a screenshot of the window, then quit.
    shots_dir = os.environ.get("ST_SMOKE_SHOTS")

    def shoot(i: int = 0):
        import re
        tabs = window.gui.tabs
        if i >= tabs.count():
            # Every tab has now had time to load: the Task Manager must list processes
            model = getattr(window.gui, '_proc_model', None)
            rows = model.rowCount() if model is not None else -1
            print(f"SMOKE task manager rows={rows}", flush=True)
            if rows == 0:
                errors.append("Task Manager listed no processes")
            finish()
            return
        tabs.setCurrentIndex(i)
        waited = [0]

        def grab():
            # The Task Manager's first list can take a while (slow machines, antivirus
            # scanning): wait for it, up to 20 s
            model = getattr(window.gui, '_proc_model', None)
            if (i == window.gui._task_tab_idx and model is not None and model.rowCount() == 0
                    and waited[0] < 16):
                waited[0] += 1
                QTimer.singleShot(1000, grab)
                return
            name = re.sub(r'[^A-Za-z0-9]+', '-', tabs.tabText(i)).strip('-').lower()
            path = os.path.join(shots_dir, f"tab-{i:02d}-{name}.png")
            window.grab().save(path)
            print(f"SMOKE screenshot {path}", flush=True)
            shoot(i + 1)
        QTimer.singleShot(4000, grab)

    # ST_SMOKE_WIDGET=1 (macOS): turn on the desktop Performance widget, check macOS put it at
    # desktop level, and screenshot it half under the app window (it must sit behind it).
    def check_widget():
        import subprocess
        from ui.perf_widget import pin_to_desktop
        w = window._perf_widget
        if w is None:
            errors.append("Desktop widget did not open")
            return
        expected = None
        try:
            import ctypes, ctypes.util
            cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library('CoreGraphics'))
            cg.CGWindowLevelForKey.restype = ctypes.c_int32
            expected = cg.CGWindowLevelForKey(18) + 1
        except Exception:
            pass
        level = pin_to_desktop(w)
        print(f"SMOKE widget level={level} expected={expected} size={w.width()}x{w.height()}",
              flush=True)
        if level is None or level != expected:
            errors.append(f"Desktop widget not at desktop level ({level} != {expected})")
        if not shots_dir:
            return
        w.grab().save(os.path.join(shots_dir, "widget.png"))
        # Small app window with the widget half under its right edge: the covered half must
        # be hidden, the other half visible on the desktop.
        saved = window.geometry()
        window.setGeometry(40, 80, 560, 420)
        w.move(window.frameGeometry().right() - 150, window.frameGeometry().top() + 100)
        window.raise_()

        def capture():
            subprocess.run(['screencapture', '-x', os.path.join(shots_dir, 'desktop-with-widget.png')],
                           timeout=20)
            window.setGeometry(saved)
        QTimer.singleShot(1500, capture)

    # The menu-bar popup: open it as a click on the ST logo would, and screenshot it
    def check_popup():
        import subprocess
        window._toggle_popup()
        popup = window._popup
        print(f"SMOKE popup visible={popup.isVisible()} at={popup.geometry().getRect()} "
              f"icon={window._tray.geometry().getRect()}", flush=True)
        if not popup.isVisible():
            errors.append("Menu-bar popup did not open")

        def capture():   # once macOS has drawn it
            if shots_dir:
                popup.grab().save(os.path.join(shots_dir, "menubar-popup.png"))
                subprocess.run(['screencapture', '-x',
                                os.path.join(shots_dir, 'desktop-with-popup.png')], timeout=20)
            popup.hide()
        QTimer.singleShot(1200, capture)

    # Closing the window (what the red button does) must keep the app in the menu bar with the
    # widget, without a Dock icon; opening the app again must bring the window back.
    def check_background():
        import subprocess
        from core import mac_appkit
        from core.osinfo import mac_app_bundle
        window.enter_background()
        app.processEvents()
        policy = mac_appkit.activation_policy()
        widget_up = window._perf_widget is not None and window._perf_widget.isVisible()
        print(f"SMOKE background hidden={window.isHidden()} dock_policy={policy} "
              f"widget_visible={widget_up}", flush=True)
        if not window.isHidden() or policy != mac_appkit.ACCESSORY or not widget_up:
            errors.append("Closing the window did not leave the app in the menu bar with the widget")
        bundle = mac_app_bundle()
        if bundle:
            subprocess.Popen(['/usr/bin/open', bundle])   # like a click in Launchpad / Finder
        else:
            window._tray_open()                            # running from source: no bundle

        def after_reopen():
            policy = mac_appkit.activation_policy()
            print(f"SMOKE reopen visible={window.isVisible()} dock_policy={policy} "
                  f"via={'open ' + bundle if bundle else 'tray'}", flush=True)
            if not window.isVisible() or policy != mac_appkit.REGULAR:
                errors.append("Opening the app again did not show the window")
        QTimer.singleShot(3000, after_reopen)

    if os.environ.get("ST_SMOKE_WIDGET") and sys.platform == "darwin":
        QTimer.singleShot(1000, lambda: window._set_perf_widget(True))
        QTimer.singleShot(4000, check_widget)
        QTimer.singleShot(7000, check_popup)
        QTimer.singleShot(9000, check_background)

    QTimer.singleShot(1500, visit_tabs)
    if shots_dir:
        os.makedirs(shots_dir, exist_ok=True)
        window.resize(1280, 800)
        window._go_gui()          # the tabs live on the GUI page, not the terminal
        QTimer.singleShot(int(seconds * 1000), shoot)
    else:
        QTimer.singleShot(int(seconds * 1000), finish)


def main():
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'assets', 'STsoftwareterminalLOGO.png.png')
    _set_process_icon(_icon_path)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if IS_WINDOWS:
        app.setFont(QFont("Segoe UI", 9))      # macOS/Linux keep their native UI font
    else:
        # The UI names Windows fonts; map them to native equivalents so the terminal
        # and detail panes stay monospaced on macOS / Linux.
        mono = "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"
        ui = "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
        for win_font in ("Consolas", "Courier New", "Lucida Console"):
            QFont.insertSubstitutions(win_font, [mono, "Courier", "Monospace"])
        QFont.insertSubstitutions("Segoe UI", [ui, "Arial"])

    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    manager   = AppManager()
    processor = CommandProcessor(manager)
    window    = MainWindow(manager, processor)
    if sys.platform == "darwin" and "--background" in sys.argv[1:]:
        window.enter_background()   # started at login: menu bar + desktop widget, no window
    else:
        window.show()

    smoke = os.environ.get("ST_SMOKE_TEST")
    if smoke:
        _smoke_test(app, window, float(smoke))
    else:
        start_auto_updates(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
