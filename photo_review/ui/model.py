"""Review state: the ordered list for the current view, navigation, and the undo stack.

Pure Python, used only from the UI thread. File work happens in `actions` on the worker thread;
results are applied here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .. import file_ops
from .. import metadata as md

SORT_OPTIONS: list[tuple[str, str]] = [
    ("name_asc", "Filename (A-Z)"),
    ("name_desc", "Filename (Z-A)"),
    ("date", "Date taken (oldest first)"),
    ("modified_asc", "File modified (oldest first)"),
    ("modified_desc", "File modified (newest first)"),
    ("rating", "Rating (highest first)"),
]
_NUM = re.compile(r"(\d+)")
# Device names that suggest a scanner, so the date may be the scan date (spec 4; decision Q23 adds
# the owner's Novatek-based slide scanner, which reports Make "MAKER NAME").
SCANNER_TERMS = (
    "epson",
    "canoscan",
    "fujitsu",
    "plustek",
    "doxie",
    "flip-pal",
    "flippal",
    "scansnap",
    "maker name",
    "novatek",
)


def looks_like_scanner(make: str, model: str) -> bool:
    device = f"{make} {model}".casefold()
    return any(term in device for term in SCANNER_TERMS)


def natural_key(name: str) -> list:
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.casefold()) for p in _NUM.split(name) if p]


def needs_metadata(sort_key: str, undated_only: bool) -> bool:
    """Date and Rating sorts, and the undated filter, need a bulk metadata read."""
    return undated_only or sort_key in ("date", "rating")


def order_paths(
    paths: Iterable[Path],
    stamps: dict[Path, md.FileStamp],
    metas: dict[Path, md.PhotoMeta],
    sort_key: str,
    undated_only: bool,
) -> list[Path]:
    """Visible list for the view. Ties are broken by filename so the order is stable."""
    items = sorted(paths, key=lambda p: natural_key(p.name))
    if undated_only:
        items = [p for p in items if p not in metas or metas[p].date.undated]
    if sort_key == "name_desc":
        return list(reversed(items))
    if sort_key == "date":
        dated = [p for p in items if p in metas and not metas[p].date.undated]
        undated = [p for p in items if p not in dated]
        dated.sort(key=lambda p: metas[p].date.value)  # stable: filename order within equal dates
        return dated + undated  # undated always at the end, by filename
    if sort_key in ("modified_asc", "modified_desc"):
        sign = -1 if sort_key == "modified_desc" else 1
        return sorted(items, key=lambda p: sign * (stamps[p].mtime_ns if p in stamps else 0))
    if sort_key == "rating":

        def rank(p: Path) -> int:  # 5..1 first, then unrated/0, then archived (-1)
            r = metas[p].rating if p in metas else None
            return 1 if r is not None and r < 0 else -(r or 0)

        return sorted(items, key=rank)
    return items


@dataclass
class UndoEntry:
    kind: str  # keep | archive | caption | tags | date
    path: Path  # where the file is after the action
    snapshot: md.Snapshot
    original_path: Path  # where the file was before the action
    list_index: int  # position in the visible list before the action
    removed: bool  # the action removed the photo from the visible list
    label: str = ""


@dataclass
class ReviewModel:
    folder: Path | None = None  # the active (parent) folder
    archived_view: bool = False
    undated_only: bool = False
    sort_key: str = "name_asc"
    paths: list[Path] = field(default_factory=list)  # everything scanned in the view folder
    order: list[Path] = field(default_factory=list)  # visible list
    index: int = 0
    stamps: dict[Path, md.FileStamp] = field(default_factory=dict)
    metas: dict[Path, md.PhotoMeta] = field(default_factory=dict)
    undo: list[UndoEntry] = field(default_factory=list)

    # ---------------------------------------------------------------- view

    @property
    def view_folder(self) -> Path | None:
        if self.folder is None:
            return None
        return file_ops.archive_folder(self.folder) if self.archived_view else self.folder

    @property
    def current(self) -> Path | None:
        return self.order[self.index] if self.order else None

    @property
    def total(self) -> int:
        return len(self.order)

    @property
    def position(self) -> int:
        return self.index + 1 if self.order else 0

    def can_back(self) -> bool:
        return self.index > 0

    def can_next(self) -> bool:
        return self.index < len(self.order) - 1

    def back(self) -> None:
        if self.can_back():
            self.index -= 1

    def next(self) -> None:
        if self.can_next():
            self.index += 1

    def go_to(self, path: Path) -> bool:
        if path in self.order:
            self.index = self.order.index(path)
            return True
        return False

    def peek(self, offset: int) -> Path | None:
        i = self.index + offset
        return self.order[i] if 0 <= i < len(self.order) else None

    def meta(self, path: Path | None) -> md.PhotoMeta | None:
        """Cached metadata, only if still valid for the file's current size and mtime."""
        if path is None:
            return None
        m = self.metas.get(path)
        if m is not None and m.stamp is not None and m.stamp == self.stamps.get(path):
            return m
        return None

    def missing_metadata(self) -> list[Path]:
        return [p for p in self.paths if self.meta(p) is None]

    # ---------------------------------------------------------------- listing

    def set_listing(self, stamps: dict[Path, md.FileStamp], keep: Path | None) -> None:
        """New scan of the view folder (folder load, toggle, Refresh)."""
        self.stamps = dict(stamps)
        self.paths = list(stamps)
        self.metas = {p: m for p, m in self.metas.items() if p in self.stamps}
        self.rebuild(keep)

    def store_metas(self, metas: dict[Path, md.PhotoMeta]) -> None:
        for p, m in metas.items():
            self.metas[p] = m
            if m.stamp is not None:
                self.stamps[p] = m.stamp

    def rebuild(self, keep: Path | None) -> None:
        """Recompute the order (sort change, filter toggle, Refresh). Keeps `keep` if visible."""
        self.order = order_paths(self.paths, self.stamps, self.metas, self.sort_key, self.undated_only)
        if keep is not None and keep in self.order:
            self.index = self.order.index(keep)
        else:
            self.index = min(self.index, max(0, len(self.order) - 1)) if keep is not None else 0

    def clear_undo(self) -> None:
        self.undo.clear()

    # ---------------------------------------------------------------- applying results

    def _remove_current(self, path: Path) -> tuple[int, bool]:
        """Remove `path` from the list; show the next photo, or the previous if it was last."""
        i = self.order.index(path)
        self.order.pop(i)
        if path in self.paths:
            self.paths.remove(path)
        self.metas.pop(path, None)
        self.stamps.pop(path, None)
        was_last = i >= len(self.order)
        self.index = max(0, min(i, len(self.order) - 1))
        return i, was_last

    def applied_keep(self, old: Path, new: Path, snap: md.Snapshot | None, meta: md.PhotoMeta) -> bool:
        """KEEP done. In the normal view the photo stays displayed (decision 28: no auto-advance).
        Kept from the archived view, the file left `_archive`, so it leaves the list.
        Returns True when the archived view is now finished."""
        if old != new:  # kept from the archived view: moved to the parent folder
            i, was_last = self._remove_current(old)
            if snap is not None:
                self.undo.append(UndoEntry("keep", new, snap, old, i, True, "KEEP"))
            return was_last or not self.order
        i = self.order.index(old)
        self.store_metas({old: meta})
        if snap is not None:
            self.undo.append(UndoEntry("keep", old, snap, old, i, False, "KEEP"))
        return False

    def applied_archive(self, old: Path, new: Path, snap: md.Snapshot) -> bool:
        i, was_last = self._remove_current(old)
        self.undo.append(UndoEntry("archive", new, snap, old, i, True, "ARCHIVE"))
        return was_last or not self.order

    def applied_edit(self, kind: str, path: Path, snap: md.Snapshot, meta: md.PhotoMeta) -> bool:
        """Caption, tags or date saved. Returns True when the photo left the filtered list."""
        i = self.order.index(path)
        self.store_metas({path: meta})
        leaves = kind == "date" and self.undated_only and not meta.date.undated
        if leaves:
            self.order.pop(i)
            self.index = max(0, min(i, len(self.order) - 1))
        self.undo.append(UndoEntry(kind, path, snap, path, i, leaves, kind.capitalize()))
        return leaves

    def applied_undo(self, entry: UndoEntry, path: Path, meta: md.PhotoMeta) -> None:
        """Undo done: the photo is back at `path`; restore its list position and show it."""
        if self.undo and self.undo[-1] is entry:
            self.undo.pop()
        if path not in self.paths:
            self.paths.append(path)
        self.store_metas({path: meta})
        if entry.removed or path not in self.order:
            self.order.insert(min(entry.list_index, len(self.order)), path)
        self.go_to(path)
