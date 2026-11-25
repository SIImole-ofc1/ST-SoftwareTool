import sys, os, ctypes
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

    QTimer.singleShot(1500, visit_tabs)
    QTimer.singleShot(int(seconds * 1000), finish)


def main():
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'assets', 'STsoftwareterminalLOGO.png.png')
    _set_process_icon(_icon_path)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if IS_WINDOWS:
        app.setFont(QFont("Segoe UI", 9))      # macOS/Linux keep their native UI font

    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    manager   = AppManager()
    processor = CommandProcessor(manager)
    window    = MainWindow(manager, processor)
    window.show()

    smoke = os.environ.get("ST_SMOKE_TEST")
    if smoke:
        _smoke_test(app, window, float(smoke))
    else:
        start_auto_updates(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
