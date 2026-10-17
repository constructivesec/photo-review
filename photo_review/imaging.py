"""Image decoding, EXIF dates, and caption rendering (Pillow only, no Qt)."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover
    HEIF_AVAILABLE = False

from .fileops import FileOpError, captioned_path

# 24 MP photos are fine at the default limit; allow very large panoramas too.
Image.MAX_IMAGE_PIXELS = 400_000_000

BAR_HEIGHT = 50
MAX_CAPTION_LEN = 50
FONT_MAX_SIZE = 32
FONT_MIN_SIZE = 10
TEXT_PADDING = 10  # pixels kept clear on each side of the caption
JPEG_QUALITY = 95

EXIF_ORIENTATION = 0x0112
EXIF_DATETIME = 0x0132
EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 0x9003


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath("photo_review", "resources", *parts)


FONT_PATH = resource_path("fonts", "DejaVuSans.ttf")


# ---------------------------------------------------------------- display decoding

def load_for_display(path: Path, max_size: int) -> Image.Image:
    """Decode a photo, apply EXIF orientation, and shrink it to at most max_size px."""
    with Image.open(path) as im:
        return prepare_for_display(im, max_size)


def prepare_for_display(im: Image.Image, max_size: int) -> Image.Image:
    """Upright, shrunk, RGB/RGBA copy of an open image (see load_for_display)."""
    if im.format == "JPEG":
        im.draft("RGB", (max_size, max_size))  # fast DCT-domain downscale
    im = ImageOps.exif_transpose(im)
    im.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or "transparency" in im.info else "RGB")
    im.load()
    return im


# ---------------------------------------------------------------- dates

def _parse_exif_date(value) -> float | None:
    if not value:
        return None
    try:
        text = value.decode() if isinstance(value, bytes) else str(value)
        return datetime.strptime(text.strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except (ValueError, OSError, OverflowError, UnicodeDecodeError):
        return None


def exif_timestamp(exif) -> float | None:
    """EXIF DateTimeOriginal, else DateTime, else None. Never raises."""
    try:
        ts = _parse_exif_date(exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL))
    except Exception:
        ts = None
    if ts is None:
        try:
            ts = _parse_exif_date(exif.get(EXIF_DATETIME))
        except Exception:
            pass
    return ts


def date_taken(path: Path) -> float:
    """EXIF DateTimeOriginal (or DateTime), falling back to the file's modified time."""
    try:
        with Image.open(path) as im:
            ts = exif_timestamp(im.getexif())
            if ts is not None:
                return ts
    except Exception:
        pass
    return modified_time(path)


def modified_time(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------- captions

def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def fit_font(text: str, max_width: int) -> tuple[ImageFont.FreeTypeFont, str]:
    """Largest font (FONT_MAX_SIZE down to FONT_MIN_SIZE) whose rendering fits max_width.

    If even the minimum size is too wide, the text is truncated with an ellipsis.
    """
    for size in range(FONT_MAX_SIZE, FONT_MIN_SIZE - 1, -1):
        font = _font(size)
        if font.getlength(text) <= max_width:
            return font, text
    font = _font(FONT_MIN_SIZE)
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return font, (text + "…") if text else ""


def render_caption(im: Image.Image, text: str) -> Image.Image:
    """Return a new image: `im` (already upright) plus a 50 px black bar with centered white text."""
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    mode = "RGBA" if has_alpha else "RGB"
    src = im.convert(mode)
    w, h = src.size
    out = Image.new(mode, (w, h + BAR_HEIGHT), (0, 0, 0, 255) if has_alpha else (0, 0, 0))
    out.paste(src, (0, 0))
    font, shown = fit_font(text, max(1, w - 2 * TEXT_PADDING))
    if shown:
        draw = ImageDraw.Draw(out)
        draw.text((w / 2, h + BAR_HEIGHT / 2), shown, font=font, fill="white", anchor="mm")
    return out


def create_captioned_copy(src: Path, text: str) -> Path:
    """Render a captioned copy of the ORIGINAL `src` and save it next to it.

    Overwrites any previous captioned copy (callers back it up first for undo).
    Returns the path of the captioned copy.
    """
    text = text.strip()
    if not text:
        raise FileOpError("Type a caption first.")
    if len(text) > MAX_CAPTION_LEN:
        raise FileOpError(f"Caption is longer than {MAX_CAPTION_LEN} characters.")
    dst = captioned_path(src)
    try:
        with Image.open(src) as im:
            exif = im.getexif()
            icc = im.info.get("icc_profile")
            upright = ImageOps.exif_transpose(im)
            out = render_caption(upright, text)
    except Exception as exc:
        raise FileOpError(f"Cannot read {src.name}: {exc}") from exc

    if EXIF_ORIENTATION in exif:
        del exif[EXIF_ORIENTATION]
    save_kwargs: dict = {}
    try:
        exif_bytes = exif.tobytes()
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
    except Exception:
        pass
    if icc:
        save_kwargs["icc_profile"] = icc

    if dst.suffix.lower() == ".png":
        fmt = "PNG"
    else:
        fmt = "JPEG"
        save_kwargs.update(quality=JPEG_QUALITY, subsampling=0)
        if out.mode != "RGB":
            out = out.convert("RGB")

    # Write to a temp file in the same folder, then atomically replace.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".~caption_", suffix=".tmp", dir=dst.parent)
        os.close(fd)
    except OSError as exc:
        raise FileOpError(f"Cannot write in {dst.parent}: {exc.strerror or exc}") from exc
    tmp = Path(tmp_name)
    try:
        try:
            out.save(tmp, fmt, **save_kwargs)
        except Exception:
            save_kwargs.pop("exif", None)  # odd EXIF can fail to serialize; drop it
            out.save(tmp, fmt, **save_kwargs)
        os.replace(tmp, dst)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise FileOpError(f"Cannot save {dst.name}: {exc.strerror or exc}") from exc
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise FileOpError(f"Cannot save {dst.name}: {exc}") from exc
    return dst
