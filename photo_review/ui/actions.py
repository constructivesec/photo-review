"""Actions run on the worker thread (they own the ExifTool process and all file operations).

Each returns plain data for the UI thread to apply to the model. No Qt here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import date_logic, file_ops, tag_logic
from .. import metadata as md
from .model import UndoEntry


@dataclass
class ActionResult:
    old_path: Path
    path: Path
    meta: md.PhotoMeta
    snapshot: md.Snapshot | None  # None: nothing was written (no undo entry)
    notes: list[str] = field(default_factory=list)


def reread(et: md.ExifToolSession, path: Path) -> md.PhotoMeta:
    return md.read_meta(et, [path])[path]


def scan(view_folder: Path) -> dict[Path, md.FileStamp]:
    stamps: dict[Path, md.FileStamp] = {}
    for p in file_ops.scan(view_folder):
        try:
            stamps[p] = md.FileStamp.of(p)
        except OSError:
            continue
    return stamps


def keep(et: md.ExifToolSession, meta: md.PhotoMeta, archived_view: bool, parent: Path) -> ActionResult:
    plan = md.plan_keep(meta)
    if archived_view:
        res = file_ops.write_then_move(et, meta, plan, parent)
        return ActionResult(meta.path, res.path, reread(et, res.path), res.snapshot)
    if plan.empty:  # already kept: no-op that still advances
        file_ops.check_unchanged(meta)
        return ActionResult(meta.path, meta.path, meta, None)
    res = file_ops.write(et, meta, plan)
    return ActionResult(meta.path, meta.path, reread(et, meta.path), res.snapshot)


def archive(et: md.ExifToolSession, meta: md.PhotoMeta, folder: Path) -> ActionResult:
    res = file_ops.write_then_move(et, meta, md.plan_archive(meta), file_ops.archive_folder(folder))
    return ActionResult(meta.path, res.path, reread(et, res.path), res.snapshot)


def save_caption(et: md.ExifToolSession, meta: md.PhotoMeta, text: str, mirror: bool) -> ActionResult:
    res = file_ops.write(et, meta, md.plan_caption(meta, text, mirror))
    return ActionResult(meta.path, meta.path, reread(et, meta.path), res.snapshot, res.notes)


def save_keywords(et: md.ExifToolSession, meta: md.PhotoMeta, keywords: list[str]) -> ActionResult:
    res = file_ops.write(et, meta, md.plan_keywords(meta, keywords))
    return ActionResult(meta.path, meta.path, reread(et, meta.path), res.snapshot)


def add_tag(et: md.ExifToolSession, meta: md.PhotoMeta, text: str) -> ActionResult:
    """Validate (raises TagError), then read-modify-write the full keyword list."""
    tag = tag_logic.normalize(text)
    return save_keywords(et, meta, tag_logic.add(meta.keywords, tag))


def remove_tag(et: md.ExifToolSession, meta: md.PhotoMeta, tag: str) -> ActionResult:
    return save_keywords(et, meta, tag_logic.remove(meta.keywords, tag))


def save_date(
    et: md.ExifToolSession,
    meta: md.PhotoMeta,
    month_text: str,
    year_text: str,
    approximate: bool,
    set_create_date: bool,
    today: date | None = None,
) -> ActionResult:
    """Validate (raises DateInputError; nothing written), then write all date fields in one call."""
    month, year = date_logic.validate(month_text, year_text, today)
    value = date_logic.new_value(meta.date.value, month, year)
    plan = md.plan_date(meta, date_logic.format_exif(value), approximate, set_create_date)
    res = file_ops.write(et, meta, plan)
    return ActionResult(meta.path, meta.path, reread(et, meta.path), res.snapshot)


def undo(et: md.ExifToolSession, entry: UndoEntry) -> ActionResult:
    """Restore previous values; move the file back first-writing metadata, like the forward action."""
    now = reread(et, entry.path)
    plan = md.restore_plan(now, entry.snapshot)
    if entry.path != entry.original_path:
        res = file_ops.write_then_move(et, now, plan, entry.original_path.parent, entry.original_path.name)
    else:
        res = file_ops.write(et, now, plan)
    return ActionResult(entry.path, res.path, reread(et, res.path), None)


def read_batch(et: md.ExifToolSession, paths: list[Path]) -> dict[Path, md.PhotoMeta]:
    return md.read_meta(et, paths)
