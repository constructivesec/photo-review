"""Integrity checks shared by tests: decoded pixels and non-edited tags must not change."""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

import pillow_heif
from PIL import Image, ImageSequence

from photo_review.metadata import ExifToolSession

pillow_heif.register_heif_opener()

VOLATILE = re.compile(
    r"^(SourceFile|ExifTool:|System:|Composite:|File:FileSize$|File:CurrentIPTCDigest$|"
    r"Photoshop:IPTCDigest$|XMP-x:XMPToolkit$)"
)
STRUCTURAL = re.compile(r"(Offset|Offsets|ByteCounts|^QuickTime:MediaData(Size|Offset)?)$")


def pixels(path: Path) -> list[tuple[str, tuple[int, int], str]]:
    out = []
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            fr = frame.copy()
            out.append((fr.mode, fr.size, hashlib.sha256(fr.tobytes()).hexdigest()))
    return out


def dump(et: ExifToolSession, path: Path) -> dict:
    data = et.read_json([path], extra=["-a", "-u"])[0]
    return {k: v for k, v in data.items() if not VOLATILE.match(k) and not STRUCTURAL.search(k)}


def assert_only_changed(before: dict, after: dict, allowed: set[str]) -> None:
    """No tag outside `allowed` changed or disappeared (new housekeeping tags are allowed)."""
    changed = [
        f"{k}: {before[k]!r} -> {after.get(k)!r}"
        for k in before
        if k not in allowed and after.get(k) != before[k]
    ]
    assert not changed, "non-edited tags changed: " + "; ".join(changed)


_TIFF_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def shift_makernote_offsets(src: Path, dst: Path, shift: int) -> None:
    """Copy src to dst with its Olympus maker-note value offsets shifted by `shift` bytes: what
    programs that move the EXIF block without fixing the maker notes leave behind."""
    data = bytearray(src.read_bytes())
    tiff = data.index(b"Exif\x00\x00") + 6
    order = "<" if data[tiff : tiff + 2] == b"II" else ">"

    def u16(o: int) -> int:
        return struct.unpack_from(order + "H", data, tiff + o)[0]

    def u32(o: int) -> int:
        return struct.unpack_from(order + "I", data, tiff + o)[0]

    def entries(ifd: int) -> list[tuple[int, int]]:
        return [(ifd + 2 + 12 * i, u16(ifd + 2 + 12 * i)) for i in range(u16(ifd))]

    exif = next(u32(e + 8) for e, tag in entries(u32(4)) if tag == 0x8769)
    maker = next(u32(e + 8) for e, tag in entries(exif) if tag == 0x927C)
    assert bytes(data[tiff + maker : tiff + maker + 5]) == b"OLYMP"
    for e, _tag in entries(maker + 8):
        if _TIFF_SIZES.get(u16(e + 2), 1) * u32(e + 4) > 4:  # value stored at an offset
            struct.pack_into(order + "I", data, tiff + e + 8, u32(e + 8) + shift)
    dst.write_bytes(bytes(data))
