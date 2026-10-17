"""Photo info for the info strip and side panel: EXIF camera fields, GPS, dates (no Qt).

Nothing here raises for bad metadata: malformed or missing EXIF just leaves fields empty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from .imaging import EXIF_IFD, exif_timestamp, modified_time, prepare_for_display

EXIF_ORIENTATION = 0x0112
EXIF_MAKE = 0x010F
EXIF_MODEL = 0x0110
GPS_IFD = 0x8825
EXIF_EXPOSURE_TIME = 0x829A
EXIF_F_NUMBER = 0x829D
EXIF_ISO = 0x8827
EXIF_FOCAL_LENGTH = 0x920A
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4

NA = "n/a"
NO_EXIF_TEXT = "No EXIF data"
SEP = "  ·  "


@dataclass
class PhotoInfo:
    date: float = 0.0
    date_is_modified: bool = True  # True when `date` is the file's modified time
    width: int = 0                 # upright (EXIF orientation applied)
    height: int = 0
    file_size: int | None = None
    has_exif: bool = False
    make: str = ""
    model: str = ""
    gps: tuple[float, float] | None = None  # (latitude, longitude) in decimal degrees
    exposure_time: float | None = None      # seconds
    f_number: float | None = None
    iso: int | None = None
    focal_length: float | None = None       # mm


# ---------------------------------------------------------------- reading

def resolve_date(exif, path: Path) -> tuple[float, bool]:
    """(timestamp, is_modified): EXIF date taken if present, else the file's modified time."""
    ts = exif_timestamp(exif) if exif is not None else None
    if ts is not None:
        return ts, False
    return modified_time(path), True


def _base_info(path: Path) -> PhotoInfo:
    info = PhotoInfo(date=modified_time(path))
    try:
        info.file_size = path.stat().st_size
    except OSError:
        pass
    return info


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable()).strip()


def _number(value) -> float | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def _coordinate(value, ref, negative: str, limit: float) -> float:
    d, m, s = (float(x) for x in value)
    deg = d + m / 60 + s / 3600
    if not math.isfinite(deg) or abs(deg) > limit:
        raise ValueError("coordinate out of range")
    return -deg if _text(ref).upper().startswith(negative) else deg


def parse_gps(gps_ifd) -> tuple[float, float] | None:
    """Decimal (lat, lon) from an EXIF GPS IFD, or None if absent or malformed."""
    try:
        lat = _coordinate(gps_ifd[GPS_LAT], gps_ifd.get(GPS_LAT_REF, "N"), "S", 90)
        lon = _coordinate(gps_ifd[GPS_LON], gps_ifd.get(GPS_LON_REF, "E"), "W", 180)
    except Exception:
        return None
    return lat, lon


def _get_exif(im: Image.Image):
    try:
        exif = im.getexif()
        return exif if len(exif) else None
    except Exception:
        return None


def _ifd(exif, tag) -> dict:
    try:
        return exif.get_ifd(tag) or {}
    except Exception:
        return {}


def info_from_image(im: Image.Image, path: Path) -> PhotoInfo:
    """Info for an already-open image. Call before draft()/thumbnail(), which change im.size."""
    info = _base_info(path)
    info.width, info.height = im.size
    exif = _get_exif(im)
    info.date, info.date_is_modified = resolve_date(exif, path)
    if exif is None:
        return info
    info.has_exif = True
    if exif.get(EXIF_ORIENTATION) in (5, 6, 7, 8):
        info.width, info.height = info.height, info.width
    info.make = _text(exif.get(EXIF_MAKE))
    info.model = _text(exif.get(EXIF_MODEL))
    info.gps = parse_gps(_ifd(exif, GPS_IFD))
    sub = _ifd(exif, EXIF_IFD)
    info.exposure_time = _number(sub.get(EXIF_EXPOSURE_TIME))
    info.f_number = _number(sub.get(EXIF_F_NUMBER))
    iso = _number(sub.get(EXIF_ISO))
    info.iso = round(iso) if iso else None
    info.focal_length = _number(sub.get(EXIF_FOCAL_LENGTH))
    return info


def read_info(path: Path) -> PhotoInfo:
    """Info for a file on disk. Never raises; unreadable files get name/size/modified only."""
    try:
        with Image.open(path) as im:
            return info_from_image(im, path)
    except Exception:
        return _base_info(path)


def load_with_info(path: Path, max_size: int) -> tuple[Image.Image, PhotoInfo]:
    """Decode for display and read info from a single open of the file."""
    with Image.open(path) as im:
        info = info_from_image(im, path)
        return prepare_for_display(im, max_size), info


# ---------------------------------------------------------------- formatting

def format_date(info: PhotoInfo) -> str:
    try:
        text = datetime.fromtimestamp(info.date).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        text = NA
    return f"{text} (modified)" if info.date_is_modified else text


def format_dimensions(info: PhotoInfo) -> str:
    if not (info.width and info.height):
        return NA
    return f"{info.width} × {info.height} ({info.width * info.height / 1e6:.1f} MP)"


def format_size(size: int | None) -> str:
    if size is None:
        return NA
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024:
            break
    return f"{value:.1f} {unit}"


def info_line(info: PhotoInfo, name: str) -> str:
    """The single-line strip below the image: date, filename, dimensions, file size."""
    return SEP.join([format_date(info), name, format_dimensions(info), format_size(info.file_size)])


def format_camera(info: PhotoInfo) -> str:
    make, model = info.make, info.model
    if make and model.lower().startswith(make.lower()):
        make = ""  # "Canon" + "Canon EOS R5" -> "Canon EOS R5"
    return " ".join(p for p in (make, model) if p) or NA


def format_gps(gps: tuple[float, float] | None) -> str:
    return f"{gps[0]:.6f}, {gps[1]:.6f}" if gps else NA


def maps_url(gps: tuple[float, float]) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={gps[0]:.6f},{gps[1]:.6f}"


def format_shutter(seconds: float | None) -> str:
    if not seconds:
        return NA
    if seconds >= 1:
        return f"{round(seconds, 1):g} s"
    n = 1 / seconds
    if seconds < 0.1 or abs(n - round(n)) < 0.05:
        return f"1/{round(n)} s"
    return f"{seconds:.1f} s"  # e.g. 0.3 s, where 1/x is not a whole number


def format_aperture(f: float | None) -> str:
    return f"f/{round(f, 1):g}" if f else NA


def format_iso(iso: int | None) -> str:
    return f"ISO {iso}" if iso else NA


def format_focal(mm: float | None) -> str:
    return f"{round(mm, 1):g} mm" if mm else NA
