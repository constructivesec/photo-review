"""Background image decoding for display (current photo, with prefetch of the next)."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from .. import export

DISPLAY_MAX = 2560  # long edge kept for display
CACHE_SIZE = 4


def decode_for_display(path: Path) -> tuple[QImage, tuple[int, int], bool]:
    """Decode the first frame, apply orientation, convert to sRGB (unconverted on failure).
    Returns (image, displayed full size, colour_converted_ok)."""
    im = export.open_first_frame(path)
    im = export.orient(im)
    full = im.size
    im.thumbnail((DISPLAY_MAX, DISPLAY_MAX), Image.Resampling.LANCZOS)
    im, ok = export.to_srgb(im, keep_alpha=True)
    if im.mode == "RGBA":
        fmt, bpp = QImage.Format.Format_RGBA8888, 4
    else:
        im = im.convert("RGB")
        fmt, bpp = QImage.Format.Format_RGB888, 3
    data = im.tobytes()
    return QImage(data, im.width, im.height, im.width * bpp, fmt).copy(), full, ok


class _Signals(QObject):
    ready = Signal(object, object, object, str)  # key, QImage | None, full size, error


class _Task(QRunnable):
    def __init__(self, key: tuple, path: Path, signals: _Signals) -> None:
        super().__init__()
        self.key, self.path, self.signals = key, path, signals

    def run(self) -> None:
        try:
            img, full, _ok = decode_for_display(self.path)
            self.signals.ready.emit(self.key, img, full, "")
        except Image.DecompressionBombError as exc:
            self.signals.ready.emit(
                self.key, None, None, f"The image is too large to display safely ({exc})."
            )
        except Exception as exc:  # noqa: BLE001 - corrupt or unreadable
            self.signals.ready.emit(self.key, None, None, str(exc) or type(exc).__name__)


class Decoder(QObject):
    """Emits ready(key, QImage|None, full_size|None, error). key = (path, size, mtime_ns)."""

    ready = Signal(object, object, object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._signals = _Signals(self)
        self._signals.ready.connect(self._on_ready)
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._pending: set[tuple] = set()

    def get(self, key: tuple) -> tuple | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def request(self, key: tuple | None) -> None:
        if key is None or key in self._cache or key in self._pending:
            return
        self._pending.add(key)
        self.pool.start(_Task(key, key[0], self._signals))

    def clear(self) -> None:
        self._cache.clear()

    def _on_ready(self, key: tuple, img: QImage | None, full: tuple | None, error: str) -> None:
        self._pending.discard(key)
        self._cache[key] = (img, full, error)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        self.ready.emit(key, img, full, error)
