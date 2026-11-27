"""Homebrew install flow for the UI: Settings button, terminal command and first-launch offer."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from core import homebrew
from ui.async_call import call_async

_busy = False


class _Progress(QObject):
    """Carries progress from the worker thread to the dialog (queued signal)."""
    changed = Signal(str, int, int)


def install_homebrew(parent, on_message: Optional[Callable[[bool, str], None]] = None,
                     on_done: Optional[Callable[[bool], None]] = None) -> None:
    """Install Homebrew with a progress window. on_message(ok, text) gets the result
    (default: a message box); on_done(ok) runs afterwards."""
    global _busy
    if _busy:
        return
    report = on_message or (lambda ok, msg: (QMessageBox.information if ok else QMessageBox.warning)(
        parent, 'Homebrew', msg))
    if homebrew.is_installed():
        report(True, f'{homebrew.version() or "Homebrew"} is already installed '
                     f'at {homebrew.brew_path()}.')
        if on_done:
            on_done(True)
        return

    _busy = True
    dlg = None
    progress = _Progress()
    if homebrew.can_use_pkg():
        dlg = QProgressDialog('Downloading Homebrew from github.com/Homebrew…', None, 0, 100, parent)
        dlg.setWindowTitle('Installing Homebrew')
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        dlg.show()

        def _on_progress(stage: str, done: int, total: int):
            if stage == 'download' and total:
                dlg.setValue(int(done * 100 / total))
                dlg.setLabelText(f'Downloading Homebrew…  {done / 1e6:.0f} / {total / 1e6:.0f} MB')
            elif stage == 'verify':
                dlg.setLabelText('Checking Homebrew\'s signature and Apple notarization…')
            elif stage == 'install':
                dlg.setRange(0, 0)
                dlg.setLabelText('Installing — macOS asks for your administrator password…')
        progress.changed.connect(_on_progress)

    def _work():
        return homebrew.install(lambda s, d, t: progress.changed.emit(s, d, t))

    def _finished(result):
        global _busy
        _busy = False
        if dlg is not None:
            dlg.close()
        ok, msg = result if isinstance(result, tuple) else (False, str(result))
        if ok and homebrew.is_installed():
            msg += ('  Use it in the ST terminal (brew install …) or in Terminal '
                    '(open a new window first).')
        report(ok, msg)
        if on_done:
            on_done(ok)

    call_async(parent, _work, _finished)


def offer_on_first_launch(parent, settings: dict, save: Callable[[], None]) -> None:
    """macOS: once, offer to install Homebrew if it isn't there. Never installs by itself."""
    if not homebrew.IS_MAC or settings.get('homebrew_offered') or homebrew.is_installed():
        return
    settings['homebrew_offered'] = True
    save()
    box = QMessageBox(parent)
    box.setWindowTitle('Homebrew')
    box.setIcon(QMessageBox.Question)
    box.setText('Install Homebrew?')
    box.setInformativeText(
        'Homebrew is the package manager most Mac developers use (brew install git, '
        'python, node …). ST-SoftwareTool can install the official version for you; macOS '
        'will ask for your administrator password.\n\n'
        'You can also do it later in Settings › Homebrew or with  >_/brew:install.')
    install = box.addButton('Install Homebrew', QMessageBox.AcceptRole)
    box.addButton('Not now', QMessageBox.RejectRole)
    box.setDefaultButton(install)
    box.exec()
    if box.clickedButton() is install:
        install_homebrew(parent)
