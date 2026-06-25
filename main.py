import sys, os, ctypes
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon
from core.manager import AppManager
from core.commands import CommandProcessor
from ui.main_window import MainWindow


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


def main():
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'assets', 'STsoftwareterminalLOGO.png.png')
    _set_process_icon(_icon_path)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))

    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    manager   = AppManager()
    processor = CommandProcessor(manager)
    window    = MainWindow(manager, processor)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
