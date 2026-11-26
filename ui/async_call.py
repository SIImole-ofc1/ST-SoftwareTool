"""Run a slow call (password dialog, waiting for a process to quit, osascript) off the UI
thread and deliver its result back on the UI thread."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class _Call(QThread):
    result = Signal(object)

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            value = self._fn()
        except Exception as exc:  # report, never crash the UI
            value = (False, str(exc))
        self.result.emit(value)


def call_async(parent, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> QThread:
    """Run fn() in a worker thread; on_done(result) runs on the UI thread afterwards.
    The thread is owned by `parent`, so it stays alive until it finishes."""
    thread = _Call(fn, parent)
    thread.result.connect(on_done)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
