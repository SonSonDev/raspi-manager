"""Run a declaration change off the GUI thread while the calling dialog waits for it."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QGuiApplication

from raspi.gui.workers import submit
from raspi.services.declare import DeclareResult


def run_declaration(fn: Callable[[], DeclareResult]) -> DeclareResult:
    """SSH goes through `submit`; a local event loop keeps the window responsive."""
    box: list[DeclareResult] = []
    loop = QEventLoop()

    def done(result: DeclareResult) -> None:
        box.append(result)
        loop.quit()

    def failed(message: str) -> None:
        box.append(DeclareResult(False, message))
        loop.quit()

    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        submit(fn, done, failed)
        if not box:
            loop.exec()
    finally:
        QGuiApplication.restoreOverrideCursor()
    return box[0]
