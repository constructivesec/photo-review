"""Image decoding helpers (shared with the display) and Export for photo book (spec section 10).

Export copies the active view's photos to an output folder. Originals are never modified.
"""

from __future__ import annotations

import csv
import io
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pillow_heif
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from . import date_logic
from . import metadata as md
from .file_ops import unique_name

pillow_heif.register_heif_opener()

FONT_PATH = md.RESOURCES / "fonts" / "DejaVuSans.ttf"
JPEG_QUALITY = 95
BAR_MIN, BAR_MAX = 50, 300
BAR_PERCENT_DEFAULT, BAR_PERCENT_MIN, BAR_PERCENT_MAX = 5, 2, 10
FONT_START_RATIO = 0.60
FONT_MIN = 12
PADDING_RATIO = 0.02
CONVERT_TO_JPEG = {".heic", ".heif", ".tif", ".tiff"}
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_SRGB_BYTES = _SRGB.tobytes()


# ---------------------------------------------------------------- decoding (display and export)


def open_first_frame(path: Path) -> Image.Image:
    """Decode the first frame/page. Raises on corrupt files and Pillow's decompression-bomb limit."""
    with Image.open(path) as im:
        im.seek(0)
        frame = im.copy()  # forces decode; copy() keeps info (icc_profile) and exif
        frame.info = dict(im.info)
        return frame


def orient(im: Image.Image) -> Image.Image:
    """Apply EXIF orientation to the pixels (HEIC rotation is already applied by pillow-heif)."""
    return ImageOps.exif_transpose(im) or im


def _xyz(colorant: object) -> tuple[float, float, float] | None:
    try:
        return tuple(float(v) for v in colorant[0])  # type: ignore[index, return-value]
    except (TypeError, IndexError, ValueError):
        return None


_SRGB_PRIMARIES = tuple(
    _xyz(getattr(_SRGB.profile, a))
    for a in ("red_colorant", "green_colorant", "blue_colorant", "media_white_point")
)


def is_srgb_profile(profile: ImageCms.ImageCmsProfile) -> bool:
    """sRGB by name, or an RGB profile whose primaries and white point match sRGB (for example
    Facebook's compact "c2" profile)."""
    try:
        if "srgb" in ImageCms.getProfileDescription(profile).lower():
            return True
        p = profile.profile
        if p.xcolor_space.strip() != "RGB":
            return False
        ours = [
            _xyz(getattr(p, a))
            for a in ("red_colorant", "green_colorant", "blue_colorant", "media_white_point")
        ]
    except Exception:  # noqa: BLE001 - unreadable profile
        return False
    if any(v is None for v in ours) or any(v is None for v in _SRGB_PRIMARIES):
        return False
    return all(
        abs(a - b) <= 0.002
        for mine, ref in zip(ours, _SRGB_PRIMARIES, strict=True)
        for a, b in zip(mine, ref, strict=True)  # type: ignore[arg-type]
    )


def is_srgb_or_untagged(im: Image.Image) -> bool:
    icc = im.info.get("icc_profile")
    if not icc:
        return True
    try:
        return is_srgb_profile(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
    except Exception:  # noqa: BLE001 - unreadable profile
        return False


def normalize_mode(im: Image.Image, keep_alpha: bool) -> Image.Image:
    """8-bit RGB (or RGBA when keep_alpha and the image has transparency)."""
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v / 256).convert("L")
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha and keep_alpha:
        return im.convert("RGBA")
    if has_alpha:
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im if im.mode == "RGB" else im.convert("RGB")


def to_srgb(im: Image.Image, keep_alpha: bool = True) -> tuple[Image.Image, bool]:
    """Convert pixels to sRGB using the embedded ICC profile. Returns (image, converted_ok).

    On failure the image is returned unconverted (display falls back; export reports it)."""
    icc = im.info.get("icc_profile")
    ok = True
    if icc and not is_srgb_or_untagged(im):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            base = im if im.mode in ("RGB", "RGBA", "CMYK", "L") else normalize_mode(im, keep_alpha)
            out_mode = "RGBA" if base.mode == "RGBA" else "RGB"
            im = ImageCms.profileToProfile(base, src, _SRGB, outputMode=out_mode) or base
        except Exception:  # noqa: BLE001 - bad profile: fall back
            ok = False
    elif im.mode == "CMYK":
        im = im.convert("RGB")
    im = normalize_mode(im, keep_alpha)
    im.info.pop("icc_profile", None)
    return im, ok


# ---------------------------------------------------------------- caption bar


def bar_height(image_height: int, percent: int = BAR_PERCENT_DEFAULT) -> int:
    """round(percent% of the oriented height), half up, clamped to 50..300 px."""
    percent = max(BAR_PERCENT_MIN, min(BAR_PERCENT_MAX, int(percent)))
    raw = (image_height * percent * 2 + 100) // 200  # integer half-up rounding
    return max(BAR_MIN, min(BAR_MAX, raw))


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def fit_caption(text: str, image_width: int, bar_h: int) -> tuple[ImageFont.FreeTypeFont, str, bool]:
    """Largest font from 60% of the bar height down to 12 px that fits the width minus padding.
    If none fits, truncate with an ellipsis at 12 px. Returns (font, text, truncated)."""
    text = " ".join(text.split())  # one line: newlines and runs of spaces become single spaces
    avail = image_width - 2 * round(image_width * PADDING_RATIO)
    size = max(FONT_MIN, round(bar_h * FONT_START_RATIO))
    while size >= FONT_MIN:
        font = _font(size)
        if font.getlength(text) <= avail:
            return font, text, False
        size -= 1
    font = _font(FONT_MIN)
    cut = text
    while cut and font.getlength(cut + "…") > avail:
        cut = cut[:-1]
    return font, (cut.rstrip() + "…") if cut else "", True


def burn_caption(im: Image.Image, text: str, percent: int) -> tuple[Image.Image, bool]:
    """Extend the canvas with a black bar and centered white text. Returns (image, truncated)."""
    w, h = im.size
    bh = bar_height(h, percent)
    out = Image.new(im.mode, (w, h + bh), (0, 0, 0, 255) if im.mode == "RGBA" else (0, 0, 0))
    out.paste(im, (0, 0))
    font, shown, truncated = fit_caption(text, w, bh)
    if shown:
        draw = ImageDraw.Draw(out)
        left, top, right, bottom = draw.textbbox((0, 0), shown, font=font)
        x = (w - (right - left)) / 2 - left
        y = h + (bh - (bottom - top)) / 2 - top  # centered by the rendered bounding box
        draw.text((x, y), shown, font=font, fill=(255, 255, 255))
    return out, truncated


# ---------------------------------------------------------------- export


@dataclass
class ExportOptions:
    sequence_prefix: bool = True
    burn_caption: bool = False
    bar_percent: int = BAR_PERCENT_DEFAULT


@dataclass
class ExportSummary:
    exported: list[str] = field(default_factory=list)
    converted: list[str] = field(default_factory=list)
    burned: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)  # collisions resolved with a suffix
    not_srgb: list[str] = field(default_factory=list)  # colour conversion failed; exported as-is
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


class ExportFolderNotEmpty(Exception):
    """The output folder already has files (decision Q16: choose another folder or cancel)."""


def folder_is_empty(folder: Path) -> bool:
    return not folder.exists() or not any(folder.iterdir())


def output_name(src: Path, index: int, total: int, options: ExportOptions) -> str:
    ext = ".jpg" if src.suffix.lower() in CONVERT_TO_JPEG else src.suffix
    name = src.stem + ext
    if options.sequence_prefix:
        width = max(4, len(str(total)))
        name = f"{index:0{width}d}_{name}"
    return name


_COPYABLE_MODES = {"RGB", "L", "RGBA", "LA", "P", "I;16", "I;16B", "I;16L", "I", "1"}


def orientation(im: Image.Image) -> int:
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except (ValueError, TypeError):
        return 1


def needs_reencode(im: Image.Image, burn: bool) -> bool:
    """False when the original bytes can be copied as-is (decision Q14): upright, sRGB or
    untagged, an RGB/greyscale mode, and no caption to burn in."""
    return burn or orientation(im) != 1 or not is_srgb_or_untagged(im) or im.mode not in _COPYABLE_MODES


def _copy_metadata(et: md.ExifToolSession, src: Path, dst: Path, size: tuple[int, int]) -> None:
    """Copy all metadata from the original; reset orientation; keep the new sRGB profile."""
    et.write(
        dst,
        [
            "-tagsFromFile",
            str(src),
            "-all:all",
            "--ICC_Profile:all",
            "--IFD1:all",  # old thumbnail no longer matches the pixels
            "-IFD0:Orientation#=1",
            "-XMP-tiff:Orientation=",
            f"-ExifIFD:ExifImageWidth={size[0]}",
            f"-ExifIFD:ExifImageHeight={size[1]}",
        ],
    )


def export_photos(
    et: md.ExifToolSession,
    items: Sequence[md.PhotoMeta],
    out_dir: Path,
    options: ExportOptions,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExportSummary:
    """Export `items` (already in the active sort order, archived photos excluded by the caller)."""
    if not folder_is_empty(out_dir):
        raise ExportFolderNotEmpty(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = ExportSummary()
    rows: list[list[str]] = []
    total = len(items)
    for i, meta in enumerate(items, 1):
        src = meta.path
        if progress:
            progress(i, total, src.name)
        if meta.is_archived:
            summary.skipped.append((src.name, "archived"))
            continue
        wanted = output_name(src, i, total, options)
        name = unique_name(out_dir, wanted)
        if name != wanted:
            summary.renamed.append(f"{src.name} -> {name}")
        dst = out_dir / name
        try:
            caption = meta.caption
            convert = src.suffix.lower() in CONVERT_TO_JPEG
            im = open_first_frame(src)
            burn = options.burn_caption and bool(caption.strip())
            if not convert and not needs_reencode(im, burn):
                with open(src, "rb") as fin, open(dst, "xb") as fout:  # byte copy (decision Q14)
                    shutil.copyfileobj(fin, fout)
                shutil.copystat(src, dst)
            else:
                is_png = dst.suffix.lower() == ".png"
                pix, ok = to_srgb(orient(im), keep_alpha=is_png)
                if not ok:
                    summary.not_srgb.append(src.name)
                if burn:
                    pix, truncated = burn_caption(pix, caption, options.bar_percent)
                    summary.burned.append(name)
                    if truncated:
                        summary.truncated.append(name)
                with open(dst, "xb") as fout:
                    if is_png:
                        pix.save(fout, "PNG", icc_profile=_SRGB_BYTES)
                    else:
                        pix.convert("RGB").save(
                            fout, "JPEG", quality=JPEG_QUALITY, icc_profile=_SRGB_BYTES, subsampling=0
                        )
                _copy_metadata(et, src, dst, pix.size)
                if convert:
                    summary.converted.append(name)
        except Exception as exc:  # noqa: BLE001 - skip and report (decision Q18)
            if dst.exists() and dst.name not in summary.exported:
                dst.unlink(missing_ok=True)
            summary.skipped.append((src.name, str(exc) or type(exc).__name__))
            continue
        summary.exported.append(name)
        tags = [k for k in meta.keywords if not k.casefold().startswith(("status:", "date:"))]
        rows.append([name, date_logic.csv_date(meta.date.value, meta.approximate), caption, "; ".join(tags)])
    with open(out_dir / "captions.csv", "x", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "date_taken", "caption", "tags"])
        writer.writerows(rows)
    return summary
