"""File operations: scanning, collision-safe moves, and metadata-then-move transactions.

File mutations are limited to moves between the active folder and `_archive`, and metadata
writes (through photo_review.metadata). Nothing is ever overwritten or deleted except the
source of a verified cross-volume copy.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from . import metadata as md

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}
ARCHIVE_DIR = "_archive"
IGNORED_NAMES = {"thumbs.db", "desktop.ini"}
_WIN_HIDDEN = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


class FileOpError(Exception):
    """A user-facing file operation failure."""


class StaleFileError(FileOpError):
    """The file changed on disk since it was loaded (decision Q20: reload and cancel)."""


# ---------------------------------------------------------------- scanning


def is_archive_folder(folder: Path) -> bool:
    return folder.name.casefold() == ARCHIVE_DIR


def archive_folder(folder: Path) -> Path:
    return folder / ARCHIVE_DIR


def scan(folder: Path) -> list[Path]:
    """Supported photos directly in `folder`: not recursive, no symlinks, no hidden/system or
    zero-byte files. Order is unspecified (sorting is done by the caller)."""
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise FileOpError(f"Cannot read the folder {folder}: {_reason(exc)}") from exc
    found: list[Path] = []
    for e in entries:
        name = e.name
        if name.startswith(".") or name.casefold() in IGNORED_NAMES:
            continue
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            if e.is_symlink() or not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size == 0:
            continue
        if sys.platform == "win32" and getattr(st, "st_file_attributes", 0) & _WIN_HIDDEN:
            continue
        found.append(Path(e.path))
    return found


# ---------------------------------------------------------------- moves


def _reason(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "permission denied (the file may be read-only or open in another program)"
    if isinstance(exc, FileNotFoundError):
        return "the file or folder was not found (it may have been moved, or a network drive disconnected)"
    if exc.errno in (errno.ENOSPC,):
        return "the disk is full"
    if exc.errno in (errno.EHOSTDOWN, errno.ENETDOWN, errno.ENETUNREACH, errno.ETIMEDOUT, errno.EIO):
        return "a network or disk error occurred"
    return exc.strerror or str(exc)


def unique_name(dest_dir: Path, name: str) -> str:
    """`name`, or `stem_1.ext`, `stem_2.ext`, ... Collision checks are case-insensitive."""
    try:
        taken = {n.casefold() for n in os.listdir(dest_dir)}
    except FileNotFoundError:
        taken = set()
    if name.casefold() not in taken:
        return name
    stem, ext = os.path.splitext(name)
    n = 1
    while f"{stem}_{n}{ext}".casefold() in taken:
        n += 1
    return f"{stem}_{n}{ext}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_verify_delete(src: Path, dst: Path) -> None:
    """Cross-volume move: copy to a new file (never overwriting), verify, then delete the source."""
    try:
        with open(src, "rb") as fin, open(dst, "xb") as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, dst)
        if src.stat().st_size != dst.stat().st_size or _sha256(src) != _sha256(dst):
            raise FileOpError(f"The copy of {src.name} did not match the original; nothing was moved.")
    except FileExistsError as exc:
        raise FileOpError(f"Cannot move {src.name}: {dst.name} already exists.") from exc
    except (OSError, FileOpError) as exc:
        dst.unlink(missing_ok=True)
        if isinstance(exc, FileOpError):
            raise
        raise FileOpError(f"Cannot copy {src.name} to {dst.parent}: {_reason(exc)}") from exc
    try:
        src.unlink()
    except OSError as exc:
        dst.unlink(missing_ok=True)  # keep exactly one copy: the original
        raise FileOpError(f"Cannot move {src.name}: {_reason(exc)}") from exc


def move(src: Path, dest_dir: Path, name: str | None = None) -> Path:
    """Move `src` into `dest_dir` (created if missing) under `name` or a numeric-suffixed variant.

    Never overwrites. Same volume: hard link + unlink (atomic no-overwrite), or rename where
    links are unsupported. Different volume: copy, verify (size + SHA-256), delete.
    """
    try:
        dest_dir.mkdir(exist_ok=True)
    except OSError as exc:
        raise FileOpError(f"Cannot create the folder {dest_dir}: {_reason(exc)}") from exc
    if not src.exists():
        raise FileOpError(f"Cannot move {src.name}: {_reason(FileNotFoundError())}")
    dst = dest_dir / unique_name(dest_dir, name or src.name)
    try:
        if sys.platform == "win32":
            os.rename(src, dst)  # Windows rename never replaces an existing file
            return dst
        os.link(src, dst)
    except FileExistsError as exc:
        raise FileOpError(f"Cannot move {src.name}: {dst.name} already exists.") from exc
    except OSError as exc:
        if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17:
            _copy_verify_delete(src, dst)
            return dst
        if exc.errno in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK) and not dst.exists():
            try:
                os.rename(src, dst)  # filesystem without hard links (some network shares)
                return dst
            except OSError as exc2:
                raise FileOpError(f"Cannot move {src.name}: {_reason(exc2)}") from exc2
        raise FileOpError(f"Cannot move {src.name}: {_reason(exc)}") from exc
    try:
        os.unlink(src)
    except OSError as exc:
        os.unlink(dst)
        raise FileOpError(f"Cannot move {src.name}: {_reason(exc)}") from exc
    return dst


# ---------------------------------------------------------------- safety checks


def check_unchanged(meta: md.PhotoMeta) -> None:
    """Before any write: the file must still exist with the size and mtime that were loaded."""
    try:
        now = md.FileStamp.of(meta.path)
    except OSError as exc:
        raise StaleFileError(f"{meta.path.name} is no longer there: {_reason(exc)}") from exc
    if meta.stamp is None or now != meta.stamp:
        raise StaleFileError(
            f"{meta.path.name} was changed by another program. It has been reloaded; try again."
        )


def check_writable(path: Path) -> None:
    """Read-only files are reported up front (ExifTool replaces the file, which can otherwise
    succeed on a read-only file in a writable folder)."""
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise FileOpError(f"Cannot open {path.name}: {_reason(exc)}") from exc
    if not mode & stat.S_IWUSR or not os.access(path, os.W_OK):
        raise FileOpError(f"{path.name} is read-only, so it cannot be changed.")


# ---------------------------------------------------------------- transactions


@dataclass
class WriteResult:
    path: Path  # where the file is now
    snapshot: md.Snapshot  # previous values of the written tags
    notes: list[str]


def write(et: md.ExifToolSession, meta: md.PhotoMeta, plan: md.WritePlan) -> WriteResult:
    """One metadata write (one ExifTool call) after the safety checks."""
    check_unchanged(meta)
    snap = md.snapshot(meta, plan.tags)
    if plan.empty:
        return WriteResult(meta.path, snap, plan.notes)
    check_writable(meta.path)
    try:
        et.write(meta.path, plan.args)
    except md.MetadataError as exc:
        raise FileOpError(f"Could not save changes to {meta.path.name}: {exc}") from exc
    return WriteResult(meta.path, snap, plan.notes)


def write_then_move(
    et: md.ExifToolSession,
    meta: md.PhotoMeta,
    plan: md.WritePlan,
    dest_dir: Path,
    name: str | None = None,
    snap: md.Snapshot | None = None,
) -> WriteResult:
    """Write metadata, then move. If the write fails, nothing moves. If the move fails, the
    previous metadata is restored; if that also fails, the error names the file and its state."""
    result = write(et, meta, plan)
    if snap is not None:
        result.snapshot = snap
    try:
        result.path = move(meta.path, dest_dir, name)
    except FileOpError as move_err:
        if plan.empty:
            raise
        rollback_snap = md.snapshot(meta, plan.tags)
        try:
            now = md.read_meta(et, [meta.path])[meta.path]
            et.write(meta.path, md.restore_plan(now, rollback_snap).args)
        except (md.MetadataError, OSError) as rb_err:
            raise FileOpError(
                f"{move_err} In addition, the earlier rating and keywords of {meta.path.name} could not "
                f"be put back ({rb_err}). The file is still in {meta.path.parent} with its NEW "
                "rating and keywords."
            ) from rb_err
        raise FileOpError(f"{move_err} Its rating and keywords were put back.") from move_err
    return result
