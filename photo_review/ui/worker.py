"""The single worker thread that owns the ExifTool process and performs all file operations.

Jobs run one at a time from a priority queue (user actions before background reads).
Results are delivered on the UI thread through the `done` signal.
"""

from __future__ import annotations

import itertools
import logging
import queue
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from .. import date_logic, export, file_ops, tag_logic
from .. import metadata as md

log = logging.getLogger(__name__)

EXPECTED = (
    md.MetadataError,
    file_ops.FileOpError,
    tag_logic.TagError,
    date_logic.DateInputError,
    export.ExportFolderNotEmpty,
    OSError,
)
USER = 0
BACKGROUND = 10


class Worker(QThread):
    done = Signal(int, object, object)  # job id, result, exception (or None)
    progress = Signal(int, int, int, str)  # job id, done, total, name
    started_ok = Signal(str)  # ExifTool version
    failed_to_start = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: queue.PriorityQueue[tuple[int, int, int, Callable[..., Any] | None]] = (
            queue.PriorityQueue()
        )
        self._ids = itertools.count(1)
        self.et: md.ExifToolSession | None = None

    def submit(
        self, fn: Callable[[md.ExifToolSession, Callable[[int, int, str], None]], Any], priority: int = USER
    ) -> int:
        """Queue `fn(et, report_progress)`; returns the job id used in `done`."""
        job_id = next(self._ids)
        self._queue.put((priority, job_id, job_id, fn))
        return job_id

    def stop(self) -> None:
        self._queue.put((-1, 0, 0, None))
        self.wait(5000)

    def run(self) -> None:
        try:
            perl_problem = md.check_perl()
            if perl_problem:
                raise md.MetadataError(perl_problem)
            self.et = md.ExifToolSession()
            self.et.start()
            self.started_ok.emit(self.et.version)
        except Exception as exc:  # noqa: BLE001
            log.exception("ExifTool failed to start")
            self.failed_to_start.emit(str(exc))
            return
        while True:
            _, _, job_id, fn = self._queue.get()
            if fn is None:
                break
            try:

                def report(i: int, n: int, name: str, _id: int = job_id) -> None:
                    self.progress.emit(_id, i, n, name)

                result = fn(self.et, report)
                self.done.emit(job_id, result, None)
            except EXPECTED as exc:
                log.warning("Job %s failed: %s: %s", job_id, type(exc).__name__, exc)
                self.done.emit(job_id, None, exc)
            except Exception as exc:  # noqa: BLE001 - delivered to the UI, never crashes the thread
                log.exception("Unexpected error in worker job %s", job_id)
                self.done.emit(job_id, None, exc)
        self.et.stop()
