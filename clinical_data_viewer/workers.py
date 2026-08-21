from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str, str)
    progress = Signal(str)
    finished = Signal()


class WorkerCancelled(RuntimeError):
    pass


class Worker(QRunnable):
    """Run a callable on QThreadPool; the callable receives this worker."""

    def __init__(self, function: Callable[[Worker], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()
        self._cancelled = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def report(self, message: str) -> None:
        if self.cancelled:
            raise WorkerCancelled()
        self.signals.progress.emit(message)

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(self)
            if not self.cancelled:
                self.signals.result.emit(result)
        except Exception as error:  # noqa: BLE001 - worker boundary reports arbitrary task failures
            if not self.cancelled:
                self.signals.error.emit(
                    str(error) or type(error).__name__, traceback.format_exc()
                )
        finally:
            self.signals.finished.emit()
