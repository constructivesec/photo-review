"""Integrity checks shared by tests: decoded pixels and non-edited tags must not change."""

from __future__ import annotations

import hashlib
import re
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
