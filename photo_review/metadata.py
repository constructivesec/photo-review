"""Caption metadata: read, reconcile and write the four standard caption fields.

Fields (see docs/CAPTION_METADATA_SPEC.md):
  F1 EXIF ImageDescription (0x010E)   F2 EXIF UserComment (0x9286)
  F3 IPTC Caption-Abstract (2:120)    F4 XMP dc:description (x-default)

EXIF is read with Pillow (raw bytes, so we control decoding). IPTC and XMP are read and
all fields are written with Exiv2 (pyexiv2), which edits metadata without touching pixels.
JPEG and PNG originals are writable; HEIC/HEIF is read-only.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import string
import tempfile
import threading
import unicodedata
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

import pyexiv2

from .fileops import FileOpError

MAX_CAPTION_BYTES = 2000  # IPTC Caption-Abstract limit, applied to the UTF-8 form
WRITABLE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

FIELD_LABELS = {
    "iptc": "IPTC Caption-Abstract",
    "xmp": "XMP dc:description",
    "user_comment": "EXIF UserComment",
    "image_description": "EXIF ImageDescription",
}
# Order used when fields disagree (IPTC first, per the spec).
PRIORITY = ["iptc", "xmp", "user_comment", "image_description"]

# Values cameras write by default; treated as blank.
CAMERA_PLACEHOLDERS = {
    "olympus digital camera", "sony dsc", "digital camera", "konica minolta digital camera",
    "dcim\\100media", "exif_jpeg_picture",
}

IPTC_UTF8 = "\x1b%G"
XMP_DEFAULT = 'lang="x-default"'

# Exiv2's XMP toolkit is not guaranteed thread-safe; serialize all pyexiv2 use.
_exiv2_lock = threading.Lock()
pyexiv2.set_log_level(4)  # mute Exiv2 warnings on stderr


@dataclass
class CaptionFields:
    image_description: str = ""
    user_comment: str = ""
    iptc: str = ""
    xmp: str = ""
    errors: list[str] = field(default_factory=list)

    def values(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in PRIORITY}


@dataclass
class CaptionChoice:
    text: str
    source: str | None      # field key the text came from, None when blank
    disagree: bool          # non-blank fields hold different values


# ---------------------------------------------------------------- normalization

def clean(value: str | None) -> str:
    """Trim whitespace and trailing NULs, NFC-normalize."""
    if not value:
        return ""
    return unicodedata.normalize("NFC", value.rstrip("\x00").strip())


def normalize(value: str | None) -> str:
    """clean(), plus known camera placeholder text counts as blank (used when reading)."""
    value = clean(value)
    return "" if value.casefold() in CAMERA_PLACEHOLDERS else value


def caption_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def is_writable_format(path: Path) -> bool:
    return path.suffix.lower() in WRITABLE_EXTENSIONS


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def decode_user_comment(raw: bytes | str | None, big_endian: bool) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        raw = raw.encode("latin-1", errors="replace")
    prefix, body = raw[:8], raw[8:]
    if prefix == b"UNICODE\x00":
        first, second = ("utf-16-be", "utf-16-le") if big_endian else ("utf-16-le", "utf-16-be")
        for enc in (first, second):
            try:
                text = body.decode(enc)
            except UnicodeDecodeError:
                continue
            if text.isprintable() or "\n" in text:
                return text
        return body.decode(first, errors="replace")
    if prefix == b"ASCII\x00\x00\x00":
        return _decode_text(body)
    if prefix.startswith(b"JIS"):
        return body.decode("iso2022_jp", errors="replace")
    if prefix == b"\x00" * 8:
        return _decode_text(body)
    return _decode_text(raw)  # no valid prefix: treat the whole value as text


# ---------------------------------------------------------------- reading

def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _xmp_alt_text(value) -> str:
    if isinstance(value, dict):
        if XMP_DEFAULT in value:
            return value[XMP_DEFAULT]
        return next(iter(value.values()), "")
    return _first(value) or ""


def _read_exif_fields(data: bytes, fields: CaptionFields) -> None:
    try:
        with warnings.catch_warnings(record=True) as caught, Image.open(io.BytesIO(data)) as im:
            warnings.simplefilter("always")
            exif = im.getexif()
            exif.get_ifd(0x8769)
            for w in caught:
                if "exif" in str(w.message).lower():
                    fields.errors.append(f"EXIF: {w.message}")
            desc = exif.get(0x010E)
            if isinstance(desc, str):  # Pillow decodes ASCII tags as Latin-1
                desc = desc.encode("latin-1", errors="replace")
            fields.image_description = normalize(_decode_text(desc) if desc else "")
            uc = exif.get_ifd(0x8769).get(0x9286)
            big = getattr(exif, "endian", "<") == ">"
            fields.user_comment = normalize(decode_user_comment(uc, big))
    except Exception as exc:
        fields.errors.append(f"EXIF: {exc}")


def _read_iptc_xmp_fields(data: bytes, fields: CaptionFields) -> None:
    with _exiv2_lock:
        try:
            img = pyexiv2.ImageData(data)
        except Exception as exc:
            fields.errors.append(f"IPTC/XMP: {exc}")
            return
        try:
            try:
                iptc = img.read_iptc(encoding="latin-1")  # Latin-1 round-trips raw bytes
                raw = _first(iptc.get("Iptc.Application2.Caption"))
                if raw:
                    raw_bytes = raw.encode("latin-1")
                    utf8 = _first(iptc.get("Iptc.Envelope.CharacterSet")) == IPTC_UTF8
                    fields.iptc = normalize(raw_bytes.decode("utf-8", errors="replace")
                                            if utf8 else _decode_text(raw_bytes))
            except Exception as exc:
                fields.errors.append(f"IPTC: {exc}")
            try:
                try:
                    xmp = img.read_xmp(encoding="utf-8")
                except UnicodeDecodeError:
                    xmp = img.read_xmp(encoding="latin-1")
                fields.xmp = normalize(_xmp_alt_text(xmp.get("Xmp.dc.description")))
            except Exception as exc:
                fields.errors.append(f"XMP: {exc}")
        finally:
            img.close()


def read_fields_from_bytes(data: bytes) -> CaptionFields:
    fields = CaptionFields()
    _read_exif_fields(data, fields)
    _read_iptc_xmp_fields(data, fields)
    return fields


def read_caption_fields(path: Path) -> CaptionFields:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return CaptionFields(errors=[exc.strerror or str(exc)])
    return read_fields_from_bytes(data)


def choose_caption(fields: CaptionFields) -> CaptionChoice:
    present = {k: v for k, v in fields.values().items() if v}
    if not present:
        return CaptionChoice("", None, False)
    disagree = len(set(present.values())) > 1
    source = next(k for k in PRIORITY if k in present)
    return CaptionChoice(present[source], source, disagree)


def fields_match(fields: CaptionFields, text: str, path: Path) -> bool:
    """True if every field that would be written already holds `text` (or all are blank when clearing)."""
    keys = PRIORITY if is_writable_format(path) else []
    return all(getattr(fields, k) == text for k in keys)


# ---------------------------------------------------------------- pixel-data checks

def _jpeg_scan_offset(data: bytes) -> int:
    """Offset of the first SOS marker (start of entropy-coded image data)."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    pos = 2
    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            raise ValueError("corrupt JPEG marker sequence")
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0xDA:
            return pos
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        pos += 2 + length
    raise ValueError("JPEG has no image data")


def _png_chunks(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        yield ctype, pos, pos + 12 + length  # chunk spans [pos, end) incl. length, type, CRC
        pos += 12 + length
        if ctype == b"IEND":
            return


def pixel_payload(data: bytes) -> bytes:
    """The encoded image data that a metadata edit must never change."""
    if data[:2] == b"\xff\xd8":
        return data[_jpeg_scan_offset(data):]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return b"".join(data[s + 8:e - 4] for t, s, e in _png_chunks(data) if t == b"IDAT")
    raise ValueError("unsupported format")


# ---------------------------------------------------------------- writing helpers

def _png_fix_crc(data: bytearray, offset: int) -> None:
    for ctype, start, end in _png_chunks(bytes(data)):
        if start <= offset < end:
            crc = zlib.crc32(bytes(data[start + 4:end - 4])) & 0xFFFFFFFF
            data[end - 4:end] = crc.to_bytes(4, "big")
            return


def _patch_user_comment(data: bytes, marker: bytes, payload: bytes) -> bytes:
    """Swap the placeholder UserComment Exiv2 wrote for the real (same-length) payload."""
    if len(marker) != len(payload) or data.count(marker) != 1:
        raise ValueError("UserComment placeholder not found exactly once")
    offset = data.index(marker)
    out = bytearray(data)
    out[offset:offset + len(marker)] = payload
    if out[:8] == b"\x89PNG\r\n\x1a\n":
        _png_fix_crc(out, offset)
    return bytes(out)


def _exif_big_endian(data: bytes) -> bool:
    with Image.open(io.BytesIO(data)) as im:
        return getattr(im.getexif(), "endian", "<") == ">"


def _iter_8bim(block: bytes, base: int):
    """Yield (resource_id, data_start, data_end) for Photoshop IRB resources."""
    pos = 0
    while pos + 12 <= len(block) and block[pos:pos + 4] == b"8BIM":
        rid = int.from_bytes(block[pos + 4:pos + 6], "big")
        name_len = block[pos + 6]
        name_total = 1 + name_len + ((1 + name_len) % 2)
        size_pos = pos + 6 + name_total
        size = int.from_bytes(block[size_pos:size_pos + 4], "big")
        start = size_pos + 4
        yield rid, base + start, base + start + size
        pos = start + size + (size % 2)


def _fix_iptc_digest(data: bytes) -> bytes:
    """If a JPEG has an IPTC digest (8BIM 0x0425), make it match the current IPTC block."""
    if data[:2] != b"\xff\xd8":
        return data
    header = b"Photoshop 3.0\x00"
    pos, end = 2, _jpeg_scan_offset(data)
    iptc = digest_at = None
    while pos < end:
        marker = data[pos + 1]
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        seg_start = pos + 4
        if marker == 0xED and data[seg_start:seg_start + len(header)] == header:
            body_start = seg_start + len(header)
            for rid, s, e in _iter_8bim(data[body_start:pos + 2 + length], body_start):
                if rid == 0x0404:
                    iptc = data[s:e]
                elif rid == 0x0425 and e - s == 16:
                    digest_at = s
        pos += 2 + length
    if iptc is None or digest_at is None:
        return data
    out = bytearray(data)
    out[digest_at:digest_at + 16] = hashlib.md5(iptc).digest()
    return bytes(out)


def apply_caption_to_bytes(data: bytes, text: str) -> bytes:
    """Return `data` (JPEG or PNG) with the caption set in all four fields, or removed if text is ''.

    Pixel data is untouched; everything else is preserved by Exiv2.
    """
    text = clean(text)
    if caption_bytes(text) > MAX_CAPTION_BYTES:
        raise FileOpError(f"Caption is longer than {MAX_CAPTION_BYTES} bytes.")

    uc_marker = uc_payload = None
    with _exiv2_lock:
        img = pyexiv2.ImageData(data)
        try:
            xmp = img.read_xmp(encoding="utf-8")
            if not text:
                img.modify_exif({"Exif.Image.ImageDescription": None,
                                 "Exif.Photo.UserComment": None}, encoding="utf-8")
                img.modify_iptc({"Iptc.Application2.Caption": None}, encoding="utf-8")
                remove = {k: None for k in ("Xmp.dc.description", "Xmp.tiff.ImageDescription",
                                            "Xmp.exif.UserComment") if k in xmp}
                if remove:
                    img.modify_xmp(remove, encoding="utf-8")
            else:
                if text.isascii():
                    uc_value = "charset=Ascii " + text
                else:
                    # Exiv2's charset conversion is platform-dependent and fails on emoji, so it
                    # writes an ASCII placeholder of the right length that we patch afterwards.
                    n = len(text.encode("utf-16-le"))
                    token = "".join(secrets.choice(string.ascii_letters) for _ in range(n))
                    uc_value = "charset=Ascii " + token
                    uc_marker = b"ASCII\x00\x00\x00" + token.encode()
                img.modify_exif({"Exif.Image.ImageDescription": text,
                                 "Exif.Photo.UserComment": uc_value}, encoding="utf-8")
                img.modify_iptc({"Iptc.Envelope.CharacterSet": IPTC_UTF8,
                                 "Iptc.Application2.Caption": text}, encoding="utf-8")
                updates = {}
                for key in ("Xmp.dc.description", "Xmp.tiff.ImageDescription", "Xmp.exif.UserComment"):
                    if key == "Xmp.dc.description" or key in xmp:
                        current = xmp.get(key)
                        alt = dict(current) if isinstance(current, dict) else {}
                        alt.pop(XMP_DEFAULT, None)
                        updates[key] = {XMP_DEFAULT: text, **alt}
                img.modify_xmp(updates, encoding="utf-8")
            out = img.get_bytes()
        finally:
            img.close()

    if uc_marker is not None:
        utf16 = "utf-16-be" if _exif_big_endian(out) else "utf-16-le"
        uc_payload = b"UNICODE\x00" + text.encode(utf16)
        out = _patch_user_comment(out, uc_marker, uc_payload)
    return _fix_iptc_digest(out)


_OUR_EXIF = {(0, 0x010E), (0x8769, 0x9286)}
_POINTER_TAGS = {0x8769, 0x8825, 0xA005, 0x014A}  # Exif, GPS, Interop, SubIFDs
_OUR_IPTC = {"Iptc.Application2.Caption", "Iptc.Envelope.CharacterSet"}
_OUR_XMP = {"Xmp.dc.description", "Xmp.tiff.ImageDescription", "Xmp.exif.UserComment"}


def _snapshot(data: bytes) -> dict:
    """All metadata values outside the caption fields, for before/after comparison."""
    snap: dict = {}
    with warnings.catch_warnings(), Image.open(io.BytesIO(data)) as im:
        warnings.simplefilter("ignore")
        exif = im.getexif()
        for ifd in (0, 0x8769, 0x8825, 0xA005):  # IFD0, Exif, GPS, Interoperability
            try:
                tags = exif if ifd == 0 else exif.get_ifd(ifd)
            except KeyError:  # Pillow raises when the Exif IFD has no Interop pointer
                tags = {}
            for tag, value in tags.items():
                # Pointer tags hold file offsets, which legitimately move; their IFDs are compared instead.
                if (ifd, tag) in _OUR_EXIF or tag in _POINTER_TAGS:
                    continue
                # MakerNote may be re-laid out by Exiv2 (offsets fixed up); require presence only.
                snap[("exif", ifd, tag)] = True if tag == 0x927C else value
        snap["icc"] = im.info.get("icc_profile")
    with _exiv2_lock:
        img = pyexiv2.ImageData(data)
        try:
            for k, v in img.read_iptc(encoding="latin-1").items():
                if k not in _OUR_IPTC:
                    snap[("iptc", k)] = v
            for k, v in img.read_xmp(encoding="latin-1").items():
                if k not in _OUR_XMP:
                    snap[("xmp", k)] = v
        finally:
            img.close()
    return snap


def verify_written(old: bytes, new: bytes, text: str) -> None:
    if pixel_payload(old) != pixel_payload(new):
        raise FileOpError("Safety check failed: image data would change. Photo left unchanged.")
    before, after = _snapshot(old), _snapshot(new)
    lost = [k for k in before if after.get(k) != before[k]]
    if lost:
        raise FileOpError(f"Safety check failed: other metadata would change ({lost[0]}). "
                          "Photo left unchanged.")
    with Image.open(io.BytesIO(new)) as im:
        im.verify()
    fields = read_fields_from_bytes(new)
    expected = normalize(text)
    bad = [FIELD_LABELS[k] for k, v in fields.values().items() if v != expected]
    if bad:
        raise FileOpError(f"Safety check failed: {', '.join(bad)} not saved. Photo left unchanged.")


def atomic_write(path: Path, data: bytes, keep_times: os.stat_result | None = None) -> None:
    """Replace `path` with `data` via a temp file in the same folder; keep mode and times."""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".~meta_", suffix=".tmp", dir=path.parent)
    except OSError as exc:
        raise FileOpError(f"Cannot write in {path.parent}: {exc.strerror or exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        if keep_times is not None:
            os.utime(path, ns=(keep_times.st_atime_ns, keep_times.st_mtime_ns))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, PermissionError):
            reason = "permission denied (file may be read-only or open in another program)"
        else:
            reason = exc.strerror or str(exc)
        raise FileOpError(f"Cannot save caption to {path.name}: {reason}") from exc


def write_caption(path: Path, text: str) -> None:
    """Set (or clear, when text is '') the caption in all four fields of a JPEG/PNG file in place."""
    if not is_writable_format(path):
        raise FileOpError(f"Captions cannot be saved into {path.suffix} files.")
    if not os.access(path, os.W_OK):
        raise FileOpError(f"Cannot save caption to {path.name}: the file is read-only.")
    try:
        st = path.stat()
        old = path.read_bytes()
    except OSError as exc:
        raise FileOpError(f"Cannot read {path.name}: {exc.strerror or exc}") from exc
    existing = read_fields_from_bytes(old)
    if existing.errors:
        raise FileOpError(f"Cannot update metadata in {path.name}: existing metadata is damaged "
                          f"({'; '.join(existing.errors)}). Photo left unchanged.")
    try:
        new = apply_caption_to_bytes(old, text)
    except FileOpError:
        raise
    except Exception as exc:
        raise FileOpError(f"Cannot update metadata in {path.name} (existing metadata could not "
                          f"be processed: {exc}). Photo left unchanged.") from exc
    verify_written(old, new, text)
    atomic_write(path, new, keep_times=st)
