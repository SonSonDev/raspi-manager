"""Run blocking SSH work off the GUI thread; deliver results back on it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

_live: set[Job] = set()  # keep jobs (and their signals) alive until they report back


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)  # lifetime managed by _live
        self._fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - any failure is reported to the UI, never swallowed
            self.signals.failed.emit(str(exc))
            return
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass  # window closed while the job was running


def submit(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_failed: Callable[[str], None] | None = None,
) -> None:
    """Run `fn` in the thread pool. Callbacks must be Slots of a QObject living in the GUI thread."""
    job = Job(fn)
    _live.add(job)

    def release(*_: object) -> None:
        _live.discard(job)

    queued = Qt.ConnectionType.QueuedConnection
    job.signals.done.connect(on_done, queued)
    if on_failed:
        job.signals.failed.connect(on_failed, queued)
    job.signals.done.connect(release, queued)
    job.signals.failed.connect(release, queued)
    QThreadPool.globalInstance().start(job)
