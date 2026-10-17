"""Application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .ui import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Photo Review")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
