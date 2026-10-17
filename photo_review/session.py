"""Review session state: the working list, navigation, YES / NO / caption actions and undo.

Pure Python (no Qt) so it can be unit tested. Every method that touches the disk
either completes and updates state, or raises FileOpError and leaves state unchanged.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union

from . import fileops, imaging
from .fileops import FileOpError

SORT_OPTIONS: list[tuple[str, str]] = [
    ("name_asc", "Filename (A-Z)"),
    ("name_desc", "Filename (Z-A)"),
    ("taken_asc", "Date taken (oldest first)"),
    ("taken_desc", "Date taken (newest first)"),
    ("modified_asc", "Date modified (oldest first)"),
    ("modified_desc", "Date modified (newest first)"),
]
SORT_KEYS = [k for k, _ in SORT_OPTIONS]
DEFAULT_SORT = "name_asc"


def sort_needs_dates(key: str) -> bool:
    return key.startswith("taken")


def sort_paths(paths: list[Path], key: str, date_lookup: Callable[[Path], float]) -> list[Path]:
    """Sort paths by `key`. Ties break by natural filename order."""
    def name(p: Path):
        return fileops.natural_key(p.name)

    if key in ("name_asc", "name_desc"):
        return sorted(paths, key=name, reverse=key == "name_desc")
    getter = date_lookup if sort_needs_dates(key) else imaging.modified_time
    by_name = sorted(paths, key=name)
    return sorted(by_name, key=getter, reverse=key.endswith("_desc"))


@dataclass
class YesAction:
    original: Path
    kept: Path
    list_index: int
    sort_key: str
    caption_original: Path | None = None
    caption_kept: Path | None = None


@dataclass
class NoAction:
    old: Path
    new: Path


@dataclass
class CaptionAction:
    photo: Path
    caption: Path
    backup: Path | None  # prior captioned copy, or None if this created a new one


Action = Union[YesAction, NoAction, CaptionAction]


@dataclass
class Result:
    message: str
    finished: bool = False  # the last photo has been handled


class ReviewSession:
    def __init__(self, folder: Path, keep_folder: Path | None = None,
                 sort_key: str = DEFAULT_SORT, hide_no: bool = False,
                 date_cache: dict[Path, float] | None = None):
        self.folder = Path(folder)
        self.keep_folder = Path(keep_folder) if keep_folder else None
        self.sort_key = sort_key if sort_key in SORT_KEYS else DEFAULT_SORT
        self.hide_no = hide_no
        self.date_cache: dict[Path, float] = dict(date_cache or {})
        self.files: list[Path] = []      # full working list, in sort order
        self.visible: list[Path] = []    # files after the Hide NO filter
        self.index = 0
        self.undo_stack: list[Action] = []
        self._backup_dir: Path | None = None

    # ------------------------------------------------------------ loading

    def load(self) -> None:
        self.files = fileops.scan_folder(self.folder)
        self._sort()
        self._refilter()
        self.index = 0
        self.clear_undo()

    def date_of(self, path: Path) -> float:
        if path not in self.date_cache:
            self.date_cache[path] = imaging.date_taken(path)
        return self.date_cache[path]

    def _sort(self) -> None:
        self.files = sort_paths(self.files, self.sort_key, self.date_of)

    def _refilter(self, keep: Path | None = None) -> None:
        if self.hide_no:
            self.visible = [p for p in self.files if not fileops.is_no(p)]
        else:
            self.visible = list(self.files)
        if keep is not None and keep in self.visible:
            self.index = self.visible.index(keep)
        self._clamp()

    def _clamp(self) -> None:
        self.index = max(0, min(self.index, len(self.visible) - 1))

    # ------------------------------------------------------------ navigation

    @property
    def current(self) -> Path | None:
        return self.visible[self.index] if self.visible else None

    @property
    def total(self) -> int:
        return len(self.visible)

    @property
    def position(self) -> int:
        """1-based position of the current photo (0 if the list is empty)."""
        return self.index + 1 if self.visible else 0

    def can_back(self) -> bool:
        return self.index > 0

    def can_next(self) -> bool:
        return self.index < len(self.visible) - 1

    def back(self) -> None:
        if self.can_back():
            self.index -= 1

    def next(self) -> None:
        if self.can_next():
            self.index += 1

    def peek(self, offset: int) -> Path | None:
        i = self.index + offset
        return self.visible[i] if 0 <= i < len(self.visible) else None

    def go_to(self, path: Path) -> bool:
        if path in self.visible:
            self.index = self.visible.index(path)
            return True
        return False

    def set_sort(self, key: str) -> None:
        if key not in SORT_KEYS:
            return
        cur = self.current
        self.sort_key = key
        self._sort()
        self._refilter(keep=cur)

    def set_hide_no(self, hide: bool) -> None:
        cur = self.current
        self.hide_no = hide
        self._refilter(keep=cur)
        if cur is not None and cur not in self.visible:
            # Current photo got hidden: show the nearest visible photo after it.
            pos = self.files.index(cur)
            after = [p for p in self.files[pos:] if p in self.visible]
            self.index = self.visible.index(after[0]) if after else len(self.visible) - 1
            self._clamp()

    def keep_folder_error(self) -> str | None:
        return fileops.validate_keep_folder(self.keep_folder, self.folder)

    # ------------------------------------------------------------ actions

    def mark_yes(self) -> Result:
        src = self.current
        if src is None:
            raise FileOpError("No photo selected.")
        error = self.keep_folder_error()
        if error:
            raise FileOpError(error)
        keep = self.keep_folder
        assert keep is not None
        fileops.ensure_folder(keep)
        dst = fileops.keep_target(src, keep)
        fileops.move_file(src, dst)

        action = YesAction(original=src, kept=dst, list_index=self.files.index(src),
                           sort_key=self.sort_key)
        message = f"Kept {dst.name}"
        cap = fileops.captioned_path(src)
        if cap.exists():
            cap_dst = fileops.unique_path(keep, cap.stem, cap.suffix)
            try:
                fileops.move_file(cap, cap_dst)
                action.caption_original, action.caption_kept = cap, cap_dst
                message += f" (and {cap_dst.name})"
            except FileOpError as exc:
                message += f". Warning: captioned copy not moved: {exc}"

        self.undo_stack.append(action)
        self.files.remove(src)
        self.date_cache.pop(src, None)
        old_index = self.index
        self._refilter()
        self.index = old_index
        self._clamp()
        return Result(message, finished=not self.visible or old_index >= len(self.visible))

    def mark_no(self) -> Result:
        src = self.current
        if src is None:
            raise FileOpError("No photo selected.")
        if fileops.is_no(src):
            finished = not self.can_next()
            self.next()
            return Result(f"{src.name} is already marked NO", finished=finished)
        dst = fileops.no_target(src)
        fileops.rename_file(src, dst)
        self.undo_stack.append(NoAction(old=src, new=dst))
        self._replace(src, dst)
        message = f"Marked NO: {dst.name}"
        if self.hide_no:
            old_index = self.index
            self._refilter()
            self.index = old_index
            self._clamp()
            return Result(message, finished=not self.visible or old_index >= len(self.visible))
        finished = not self.can_next()
        self.next()
        return Result(message, finished=finished)

    def add_caption(self, text: str) -> Result:
        src = self.current
        if src is None:
            raise FileOpError("No photo selected.")
        cap = fileops.captioned_path(src)
        backup = None
        if cap.exists():
            backup = self._backup(cap)
        try:
            out = imaging.create_captioned_copy(src, text)
        except FileOpError:
            if backup is not None:
                backup.unlink(missing_ok=True)
            raise
        self.undo_stack.append(CaptionAction(photo=src, caption=out, backup=backup))
        verb = "Replaced" if backup else "Saved"
        return Result(f"{verb} captioned copy: {out.name}")

    # ------------------------------------------------------------ undo

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def undo(self) -> Result:
        if not self.undo_stack:
            raise FileOpError("Nothing to undo.")
        action = self.undo_stack[-1]
        if isinstance(action, YesAction):
            result = self._undo_yes(action)
        elif isinstance(action, NoAction):
            result = self._undo_no(action)
        else:
            result = self._undo_caption(action)
        self.undo_stack.pop()
        return result

    def _undo_yes(self, a: YesAction) -> Result:
        fileops.move_file(a.kept, a.original)
        note = ""
        if a.caption_kept is not None and a.caption_original is not None:
            try:
                fileops.move_file(a.caption_kept, a.caption_original)
            except FileOpError as exc:
                note = f". Warning: captioned copy not restored: {exc}"
        self.files.insert(min(a.list_index, len(self.files)), a.original)
        if a.sort_key != self.sort_key:
            self._sort()  # sort changed since the YES; the old position is meaningless
        self._refilter()
        shown = self.go_to(a.original)
        msg = f"Undo: restored {a.original.name}"
        if not shown:
            msg += " (hidden by 'Hide NO photos')"
        return Result(msg + note)

    def _undo_no(self, a: NoAction) -> Result:
        fileops.rename_file(a.new, a.old)
        self._replace(a.new, a.old)
        self._refilter()
        self.go_to(a.old)
        return Result(f"Undo: renamed back to {a.old.name}")

    def _undo_caption(self, a: CaptionAction) -> Result:
        try:
            if a.backup is not None:
                shutil.copy2(a.backup, a.caption)
                a.backup.unlink(missing_ok=True)
                msg = f"Undo: restored previous {a.caption.name}"
            else:
                a.caption.unlink(missing_ok=True)
                msg = f"Undo: removed {a.caption.name}"
        except OSError as exc:
            raise FileOpError(f"Cannot undo caption: {exc.strerror or exc}") from exc
        self.go_to(a.photo)
        return Result(msg)

    # ------------------------------------------------------------ helpers

    def _replace(self, old: Path, new: Path) -> None:
        self.files[self.files.index(old)] = new
        if old in self.visible:
            self.visible[self.visible.index(old)] = new
        if old in self.date_cache:
            self.date_cache[new] = self.date_cache.pop(old)

    def _backup(self, path: Path) -> Path:
        """Copy a captioned file into a session temp folder so caption undo can restore it."""
        if self._backup_dir is None:
            self._backup_dir = Path(tempfile.mkdtemp(prefix="photo_review_undo_"))
        fd, name = tempfile.mkstemp(dir=self._backup_dir, suffix=path.suffix)
        os.close(fd)
        dst = Path(name)
        try:
            shutil.copy2(path, dst)
        except OSError as exc:
            dst.unlink(missing_ok=True)
            raise FileOpError(f"Cannot back up {path.name}: {exc.strerror or exc}") from exc
        return dst

    def clear_undo(self) -> None:
        self.undo_stack.clear()
        if self._backup_dir is not None:
            shutil.rmtree(self._backup_dir, ignore_errors=True)
            self._backup_dir = None

    def close(self) -> None:
        self.clear_undo()
