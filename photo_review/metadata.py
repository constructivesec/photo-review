"""ExifTool wrapper. All EXIF, XMP and IPTC reads and writes go through this module.

One persistent ExifTool process (-stay_open) is used. Arguments are sent through
ExifTool's argument file on stdin, one per line, never through a shell. Any argument
containing characters that would break that format (newlines, backslashes, tabs,
leading or trailing spaces) is sent as an ExifTool "#[CSTR]" C-string line.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

import exiftool  # PyExifTool

ROOT = Path(__file__).resolve().parent.parent

# Flags used for every write (spec section 11).
WRITE_FLAGS: tuple[str, ...] = ("-overwrite_original", "-P")
# UTF-8 file names on Windows; values are UTF-8 by default.
COMMON_ARGS: tuple[str, ...] = ("-charset", "filename=utf8")

_CSTR_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\a": "\\a", "\b": "\\b", "\f": "\\f"}


class MetadataError(Exception):
    """A user-facing metadata read or write failure."""


def find_exiftool() -> Path:
    """Locate the bundled ExifTool (frozen app, then vendor/, then $PHOTO_REVIEW_EXIFTOOL)."""
    override = os.environ.get("PHOTO_REVIEW_EXIFTOOL")
    if override:
        return Path(override)
    name = "exiftool.exe" if sys.platform == "win32" else "exiftool"
    sub = "windows" if sys.platform == "win32" else "unix"
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "exiftool" / name)
    candidates.append(ROOT / "vendor" / "exiftool" / sub / name)
    for c in candidates:
        if c.is_file():
            return c
    raise MetadataError(
        "ExifTool not found. Run: python tools/fetch_exiftool.py "
        f"(looked in: {', '.join(str(c) for c in candidates)})"
    )


def encode_arg(arg: str) -> str:
    """Encode one argument for ExifTool's line-based argument file."""
    if "\x00" in arg:
        raise MetadataError("Text contains a NUL character, which cannot be stored.")
    needs_cstr = any(ch in arg for ch in _CSTR_ESCAPES) or arg != arg.strip() or arg.startswith("#")
    if not needs_cstr:
        return arg
    out = []
    for ch in arg:
        if ch in _CSTR_ESCAPES:
            out.append(_CSTR_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise MetadataError(f"Text contains an unsupported control character (U+{ord(ch):04X}).")
        else:
            out.append(ch)
    return "#[CSTR]" + "".join(out)


class ExifToolSession:
    """A single persistent ExifTool process. Not thread-safe: use from one worker thread."""

    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable or find_exiftool()
        self._et = exiftool.ExifTool(
            executable=str(self.executable), common_args=list(COMMON_ARGS), encoding="utf-8"
        )
        self._lock = threading.Lock()  # guards against accidental cross-thread use
        self.last_warnings = ""

    def __enter__(self) -> ExifToolSession:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        if not self._et.running:
            self._et.run()

    def stop(self) -> None:
        if self._et.running:
            self._et.terminate()

    @property
    def version(self) -> str:
        return self.execute("-ver").strip()

    def execute(self, *args: str) -> str:
        """Run one ExifTool command. Raises MetadataError on a non-zero exit status."""
        encoded = [encode_arg(a) for a in args]
        with self._lock:
            self.start()
            try:
                out = self._et.execute(*encoded)
            except Exception as exc:  # process died, I/O error
                self.stop()
                raise MetadataError(f"ExifTool failed: {exc}") from exc
            status, err = self._et.last_status, self._et.last_stderr or ""
        if status != 0:
            raise MetadataError(err.strip() or f"ExifTool exited with status {status}")
        self.last_warnings = err.strip()
        return out

    def execute_bytes(self, *args: str) -> bytes:
        """Run one ExifTool command and return raw stdout (for -b binary extraction)."""
        encoded = [encode_arg(a) for a in args]
        with self._lock:
            self.start()
            try:
                out = self._et.execute(*encoded, raw_bytes=True)
            except Exception as exc:
                self.stop()
                raise MetadataError(f"ExifTool failed: {exc}") from exc
        return out

    def read_json(
        self,
        paths: Sequence[Path | str],
        tags: Iterable[str] = (),
        extra: Iterable[str] = (),
        numeric: bool = False,
    ) -> list[dict]:
        """Read tags (all if none given) as JSON, keyed "Group1:Tag" (-G1).

        Values are ExifTool's display form unless numeric=True (-n). Append "#" to a single
        tag name for its raw value.
        """
        if not paths:
            return []
        args = [
            "-j",
            "-G1",
            *(["-n"] if numeric else []),
            *extra,
            *(f"-{t}" for t in tags),
            *(str(p) for p in paths),
        ]
        out = self.execute(*args)
        return json.loads(out) if out.strip() else []

    def write(self, path: Path | str, assignments: Sequence[str]) -> str:
        """Write all assignments ("-TAG=value", "-TAG+=value", ...) to one file in one call."""
        out = self.execute(*WRITE_FLAGS, *assignments, str(path))
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise MetadataError(out.strip() or "ExifTool did not update the file")
        return out
