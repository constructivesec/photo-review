"""Background workers: photo decoding plus EXIF info (with a small cache) and EXIF date scanning."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from . import exif, imaging

DISPLAY_MAX = 3200  # long-edge pixels kept for display (enough for 4K screens)
CACHE_SIZE = 6


def pil_to_qimage(im) -> QImage:
    if im.mode == "RGBA":
        fmt, bpp = QImage.Format.Format_RGBA8888, 4
    else:
        im = im.convert("RGB")
        fmt, bpp = QImage.Format.Format_RGB888, 3
    data = im.tobytes()
    return QImage(data, im.width, im.height, im.width * bpp, fmt).copy()


class _Signals(QObject):
    loaded = Signal(object, object, str, object)  # path, QImage | None, error, PhotoInfo
    dates_progress = Signal(int, int, int)  # generation, done, total
    dates_done = Signal(int, object)  # generation, {path: timestamp}


class _DecodeTask(QRunnable):
    def __init__(self, path: Path, signals: _Signals):
        super().__init__()
        self.path, self.signals = path, signals

    def run(self) -> None:
        try:
            im, info = exif.load_with_info(self.path, DISPLAY_MAX)
            self.signals.loaded.emit(self.path, pil_to_qimage(im), "", info)
        except Exception as exc:  # corrupt / unreadable / vanished
            self.signals.loaded.emit(self.path, None, str(exc) or type(exc).__name__,
                                     exif.read_info(self.path))


class _DatesTask(QRunnable):
    def __init__(self, generation: int, paths: list[Path], signals: _Signals):
        super().__init__()
        self.generation, self.paths, self.signals = generation, paths, signals

    def run(self) -> None:
        result = {}
        total = len(self.paths)
        for i, p in enumerate(self.paths, 1):
            result[p] = imaging.date_taken(p)
            if i % 50 == 0:
                self.signals.dates_progress.emit(self.generation, i, total)
        self.signals.dates_done.emit(self.generation, result)


class ImageLoader(QObject):
    """Decodes photos off the UI thread. Emits `ready(path, QImage|None, error, PhotoInfo)`."""

    ready = Signal(object, object, str, object)
    dates_progress = Signal(int, int, int)
    dates_done = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._signals = _Signals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.dates_progress.connect(self.dates_progress)
        self._signals.dates_done.connect(self.dates_done)
        self._cache: OrderedDict[Path, tuple[QImage | None, str, exif.PhotoInfo]] = OrderedDict()
        self._pending: set[Path] = set()

    def get(self, path: Path) -> tuple[QImage | None, str, exif.PhotoInfo] | None:
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        return None

    def request(self, path: Path | None) -> None:
        if path is None or path in self._cache or path in self._pending:
            return
        self._pending.add(path)
        self.pool.start(_DecodeTask(path, self._signals))

    def rename(self, old: Path, new: Path) -> None:
        if old in self._cache:
            self._cache[new] = self._cache.pop(old)

    def clear(self) -> None:
        self._cache.clear()

    def scan_dates(self, generation: int, paths: list[Path]) -> None:
        self.pool.start(_DatesTask(generation, list(paths), self._signals))

    def _on_loaded(self, path: Path, img, error: str, info) -> None:
        self._pending.discard(path)
        self._cache[path] = (img, error, info)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        self.ready.emit(path, img, error, info)
