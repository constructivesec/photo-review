"""File-system operations: scanning, naming rules, collision-safe moves and renames.

Nothing in this module ever overwrites an existing file or deletes an original.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
from pathlib import Path

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".heif"}
NO_SUFFIX = "_NO"
CAPTION_SUFFIX = "_captioned"
HEIF_EXTENSIONS = {".heic", ".heif"}


class FileOpError(Exception):
    """A user-facing file operation failure. The message is shown in the status bar."""


# ---------------------------------------------------------------- naming rules

def is_no(path: Path) -> bool:
    return path.stem.endswith(NO_SUFFIX)


def base_stem(path: Path) -> str:
    """Stem with a trailing `_NO` removed."""
    stem = path.stem
    return stem[: -len(NO_SUFFIX)] if stem.endswith(NO_SUFFIX) else stem


def is_captioned_copy(path: Path) -> bool:
    stem = path.stem
    return stem.endswith(CAPTION_SUFFIX) or stem.endswith(CAPTION_SUFFIX + NO_SUFFIX)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS and not is_captioned_copy(path)


def caption_extension(path: Path) -> str:
    """Output extension for a captioned copy: HEIC/HEIF become .jpg, others keep theirs."""
    return ".jpg" if path.suffix.lower() in HEIF_EXTENSIONS else path.suffix


def captioned_path(path: Path) -> Path:
    """`<base stem>_captioned.<ext>` next to the original.

    The `_NO` marker is dropped from the base, so a photo and its NO'd version
    share one captioned copy and the link survives NO / YES.
    """
    return path.with_name(base_stem(path) + CAPTION_SUFFIX + caption_extension(path))


def unique_path(folder: Path, stem: str, ext: str, tail: str = "") -> Path:
    """First free `<stem>[_N]<tail><ext>` in `folder` (N = 1, 2, ...)."""
    candidate = folder / f"{stem}{tail}{ext}"
    n = 1
    while candidate.exists():
        candidate = folder / f"{stem}_{n}{tail}{ext}"
        n += 1
    return candidate


def no_target(path: Path) -> Path:
    """Target name for marking NO: `IMG.jpg` -> `IMG_NO.jpg` (or `IMG_1_NO.jpg` on collision)."""
    return unique_path(path.parent, path.stem, path.suffix, NO_SUFFIX)


def keep_target(path: Path, keep_folder: Path) -> Path:
    """Target in the Keep folder with `_NO` stripped and a numeric suffix on collision."""
    return unique_path(keep_folder, base_stem(path), path.suffix)


# ---------------------------------------------------------------- scanning

_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> list:
    """Case-insensitive natural sort key, so IMG_2 sorts before IMG_10."""
    return [int(p) if p.isdigit() else p.casefold() for p in _NUM_RE.split(name)]


def scan_folder(folder: Path) -> list[Path]:
    """Supported photos directly inside `folder` (not recursive), in natural name order."""
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        raise FileOpError(f"Cannot read folder {folder}: {exc.strerror or exc}") from exc
    files = []
    for entry in entries:
        try:
            if entry.is_file() and not entry.name.startswith("."):
                p = Path(entry.path)
                if is_supported(p):
                    files.append(p)
        except OSError:
            continue
    files.sort(key=lambda p: natural_key(p.name))
    return files


def same_folder(a: Path, b: Path) -> bool:
    def norm(p: Path) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    return norm(a) == norm(b)


def validate_keep_folder(keep: Path | None, source: Path | None) -> str | None:
    """Return an error message, or None if `keep` is usable."""
    if keep is None or not str(keep).strip():
        return "No Keep folder set."
    if not keep.is_absolute():
        return "Keep folder must be a full path."
    if source is not None and same_folder(keep, source):
        return "Keep folder cannot be the same as the photo folder."
    if keep.exists():
        if not keep.is_dir():
            return f"Keep folder is not a folder: {keep}"
        if not os.access(keep, os.W_OK):
            return f"Keep folder is not writable: {keep}"
        return None
    # Missing: it is created on first move, as long as some ancestor exists and is writable.
    parent = keep.parent
    while not parent.exists():
        if parent.parent == parent:
            return f"Keep folder location does not exist: {keep}"
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        return f"Cannot create Keep folder in {parent}"
    return None


# ---------------------------------------------------------------- mutations

def _describe(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "permission denied (file may be read-only or open in another program)"
    if isinstance(exc, FileNotFoundError):
        return "file not found (it may have been moved or the network share disconnected)"
    return exc.strerror or str(exc)


def _is_cross_device(exc: OSError) -> bool:
    # POSIX: EXDEV. Windows: ERROR_NOT_SAME_DEVICE (17).
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_verified(src: Path, dst: Path) -> None:
    """Copy src to a NEW file dst (never overwrites), then verify size and checksum."""
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    if src.stat().st_size != dst.stat().st_size or _sha256(src) != _sha256(dst):
        dst.unlink(missing_ok=True)
        raise FileOpError(f"Copy verification failed for {src.name}; original left in place.")


def move_file(src: Path, dst: Path) -> None:
    """Move src to dst without ever overwriting. Handles moves across volumes."""
    if dst.exists():
        raise FileOpError(f"Cannot move {src.name}: {dst} already exists.")
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if not _is_cross_device(exc):
            raise FileOpError(f"Cannot move {src.name}: {_describe(exc)}") from exc
    try:
        _copy_verified(src, dst)
    except FileExistsError as exc:
        raise FileOpError(f"Cannot move {src.name}: {dst} already exists.") from exc
    except OSError as exc:
        dst.unlink(missing_ok=True)
        raise FileOpError(f"Cannot copy {src.name} to {dst.parent}: {_describe(exc)}") from exc
    try:
        src.unlink()
    except OSError as exc:
        # Keep the original intact; remove the copy so we don't end up with duplicates.
        dst.unlink(missing_ok=True)
        raise FileOpError(f"Cannot move {src.name}: {_describe(exc)}") from exc


def rename_file(src: Path, dst: Path) -> None:
    """Rename within a folder without overwriting."""
    move_file(src, dst)


def ensure_folder(folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FileOpError(f"Cannot create folder {folder}: {_describe(exc)}") from exc
