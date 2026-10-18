"""Application entry point: logging, single instance, global exception handler."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path
from types import TracebackType

from .config import Config, config_dir

log = logging.getLogger("photo_review")


def setup_logging(folder: Path) -> Path:
    """Rotating debug log (1 MB x 3) in the config directory. Paths only, never caption text."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "photo_review.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    return path


def main() -> int:
    from PySide6.QtCore import QEvent, QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import __version__
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Photo Review")
    app.setApplicationVersion(__version__)
    cdir = config_dir()
    setup_logging(cdir / "logs")
    log.info("Photo Review %s starting", __version__)

    lock = QLockFile(str(cdir / "photo_review.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Photo Review", "Photo Review is already running.")
        return 0

    def excepthook(etype: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(etype, value, tb)))
        try:
            QMessageBox.critical(
                None,
                "Something went wrong",
                f"An unexpected error occurred:\n\n{value}\n\nThe app will keep running. "
                "Details are in the log (File > Open log folder).",
            )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = excepthook
    threading.excepthook = lambda a: log.error(
        "Unhandled thread exception:\n%s",
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)),
    )

    window = MainWindow(Config.load())
    window.show()
    code = app.exec()
    # Delete the window before the QApplication is torn down (avoids a PySide crash at exit).
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    lock.unlock()
    return code
