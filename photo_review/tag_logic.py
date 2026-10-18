"""Tag (keyword) rules (spec section 8). Pure functions, no I/O."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

ARCHIVE = "status:archive"
APPROXIMATE = "date:approximate"
SYSTEM_TAGS = (ARCHIVE, APPROXIMATE)
RESERVED_PREFIXES = ("status", "date")
QUICK_PREFIXES = ("person", "event", "place")
MAX_BYTES = 64  # decision Q11: bytes of UTF-8, matching the IPTC Keywords limit
HISTORY_MAX = 500


class TagError(ValueError):
    """Invalid tag input. The message is shown in the status bar."""


def _word_ok(part: str) -> bool:
    return bool(part) and all(ch.isalpha() or ch.isdigit() or ch == "-" for ch in part)


def normalize(text: str) -> str:
    """Normalize user input to a tag, or raise TagError.

    Lowercase, NFC, spaces become hyphens; Unicode letters, digits and hyphens only; at most one
    "prefix:" ; no reserved prefixes; at most 64 bytes.
    """
    t = unicodedata.normalize("NFC", text).strip().lower()
    if not t:
        raise TagError("Type a tag first.")
    if "," in t or ";" in t:
        raise TagError("Tags cannot contain commas or semicolons. Add one tag at a time.")
    t = re.sub(r"\s+", "-", t)
    parts = t.split(":")
    if len(parts) > 2:
        raise TagError("A tag can have only one prefix, for example person:emma.")
    if len(parts) == 2:
        prefix, name = parts
        if not prefix or not name:
            raise TagError("Write a prefix and a name, for example person:emma.")
        if prefix in RESERVED_PREFIXES:
            raise TagError(f'"{prefix}:" tags are managed by the app and cannot be added by hand.')
    if not all(_word_ok(p) for p in parts):
        raise TagError("Tags can contain only letters, numbers and hyphens.")
    if len(t.encode("utf-8")) > MAX_BYTES:
        raise TagError(f"That tag is too long (at most {MAX_BYTES} bytes).")
    return t


def is_system(keyword: str) -> bool:
    return keyword.casefold() in SYSTEM_TAGS


def is_user_tag(keyword: str) -> bool:
    """A tag this app's rules produce. Other keywords (other apps) are read-only (decision Q10)."""
    if is_system(keyword):
        return False
    try:
        return normalize(keyword) == keyword
    except TagError:
        return False


def merge_keywords(xmp: Sequence[str], iptc: Sequence[str]) -> list[str]:
    """Union of XMP Subject and IPTC Keywords, deduped case-insensitively.

    Order: XMP first, then IPTC-only keywords. The XMP spelling wins (decision Q12).
    """
    seen: set[str] = set()
    out: list[str] = []
    for k in [*xmp, *iptc]:
        key = k.casefold()
        if k and key not in seen:
            seen.add(key)
            out.append(k)
    return out


def add(keywords: Sequence[str], tag: str) -> list[str]:
    """Add a normalized tag (no duplicate, case-insensitive)."""
    return merge_keywords(list(keywords), [tag])


def remove(keywords: Sequence[str], tag: str) -> list[str]:
    """Remove a user tag. Read-only keywords are never removed."""
    if not is_user_tag(tag):
        raise TagError(f'"{tag}" is not managed by this app and is left unchanged.')
    return [k for k in keywords if k != tag]


def update_history(history: Sequence[str], used: Iterable[str]) -> list[str]:
    """Most recently used first, deduped, capped at HISTORY_MAX."""
    new = merge_keywords([t for t in used if is_user_tag(t)], list(history))
    return new[:HISTORY_MAX]


def suggestions(
    prefix_text: str, folder_tags: Iterable[str], history: Iterable[str], limit: int = 20
) -> list[str]:
    """Autocomplete candidates from the folder's tags and the tag history."""
    needle = prefix_text.strip().lower()
    pool = merge_keywords([t for t in folder_tags if is_user_tag(t)], [t for t in history if is_user_tag(t)])
    return [t for t in pool if needle in t][:limit]
