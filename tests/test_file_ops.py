"""File operations: scanning, moves, safety checks, metadata-then-move with rollback."""

from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from photo_review import file_ops as fo
from photo_review import metadata as md
from photo_review.metadata import ExifToolSession

Fixture = Callable[..., Path]


def read(et: ExifToolSession, p: Path) -> md.PhotoMeta:
    return md.read_meta(et, [p])[p]


# ---------------------------------------------------------------- scanning


def test_scan_filters(fixture_dir: Path) -> None:
    names = {p.name for p in fo.scan(fixture_dir)}
    for ignored in ["._readonly.jpg", ".hidden.jpg", "Thumbs.db", "empty.jpg", "IMG_0001.mov", "symlink.jpg"]:
        assert ignored not in names, ignored
    assert "archived.jpg" not in names  # _archive is never part of the normal view
    for expected in [
        "iphone.heic",
        "scan.tif",
        "multipage.tif",
        "no_metadata.png",
        "corrupt.jpg",
        "readonly.jpg",
        "Grand-mère à la plage 1962.v2.final.jpg",
    ]:
        assert expected in names, expected
    assert {p.name for p in fo.scan(fo.archive_folder(fixture_dir))} == {"archived.jpg"}


def test_scan_missing_folder(tmp_path: Path) -> None:
    assert fo.scan(tmp_path / "nope") == []


def test_archive_folder_detection(tmp_path: Path) -> None:
    assert fo.is_archive_folder(tmp_path / "_archive") and fo.is_archive_folder(tmp_path / "_Archive")
    assert not fo.is_archive_folder(tmp_path / "photos")


# ---------------------------------------------------------------- moves


def test_unique_name_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Name.JPG").write_bytes(b"x")
    assert fo.unique_name(tmp_path, "name.jpg") == "name_1.jpg"
    (tmp_path / "NAME_1.jpg").write_bytes(b"x")
    assert fo.unique_name(tmp_path, "name.jpg") == "name_2.jpg"
    assert fo.unique_name(tmp_path, "other.jpg") == "other.jpg"
    assert fo.unique_name(tmp_path / "missing", "a.jpg") == "a.jpg"


@pytest.mark.parametrize("name", ["plain.jpg", "with space.jpg", "Grand-mère 日本.v2.final.jpg"])
def test_move_creates_folder_and_never_overwrites(tmp_path: Path, name: str) -> None:
    src = tmp_path / name
    src.write_bytes(b"original")
    dest = tmp_path / "_archive"
    dest.mkdir()
    (dest / name.upper()).write_bytes(b"existing")
    moved = fo.move(src, dest)
    assert not src.exists() and moved.read_bytes() == b"original"
    assert moved.name != name.upper() and (dest / name.upper()).read_bytes() == b"existing"


def test_move_cross_volume_copies_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "a.jpg"
    src.write_bytes(os.urandom(300_000))
    data = src.read_bytes()
    monkeypatch.setattr(os, "link", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")))
    moved = fo.move(src, tmp_path / "other")
    assert not src.exists() and moved.read_bytes() == data


def test_move_cross_volume_verification_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.jpg"
    src.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")))
    monkeypatch.setattr(fo, "_sha256", lambda p: str(p))
    with pytest.raises(fo.FileOpError, match="did not match"):
        fo.move(src, tmp_path / "other")
    assert src.read_bytes() == b"data" and not (tmp_path / "other" / "a.jpg").exists()


def test_move_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "a.jpg"
    src.write_bytes(b"data")
    monkeypatch.setattr(
        os, "link", lambda a, b: (_ for _ in ()).throw(PermissionError(errno.EACCES, "denied"))
    )
    with pytest.raises(fo.FileOpError, match="permission denied"):
        fo.move(src, tmp_path / "other")
    assert src.exists()


def test_move_missing_source(tmp_path: Path) -> None:
    with pytest.raises(fo.FileOpError, match="not found"):
        fo.move(tmp_path / "gone.jpg", tmp_path / "x")


# ---------------------------------------------------------------- safety checks


def test_stale_file_detected(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    m = read(et, p)
    os.utime(p, ns=(0, p.stat().st_mtime_ns + 5_000_000_000))
    with pytest.raises(fo.StaleFileError):
        fo.write(et, m, md.plan_keep(m))
    assert read(et, p).rating is None


def test_deleted_file_detected(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    m = read(et, p)
    p.unlink()
    with pytest.raises(fo.StaleFileError, match="no longer there"):
        fo.write(et, m, md.plan_keep(m))


def test_read_only_file_is_reported_and_untouched(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("readonly.jpg")
    os.chmod(p, 0o444)
    m = read(et, p)
    before = p.read_bytes()
    with pytest.raises(fo.FileOpError, match="read-only"):
        fo.write(et, m, md.plan_keep(m))
    assert p.read_bytes() == before


# ---------------------------------------------------------------- transactions


def test_archive_writes_metadata_before_move(
    et: ExifToolSession, fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = fixture("lightroom.jpg")
    m = read(et, p)
    seen: dict[str, object] = {}
    real_move = fo.move

    def spy(src: Path, dest: Path, name: str | None = None) -> Path:
        seen["rating_at_move"] = read(et, src).rating
        return real_move(src, dest, name)

    monkeypatch.setattr(fo, "move", spy)
    res = fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(p.parent))
    assert seen["rating_at_move"] == -1
    assert res.path == p.parent / "_archive" / "lightroom.jpg" and not p.exists()
    assert res.snapshot == {md.RATING: "3", md.SUBJECT: ["family", "Christmas", "Lightroom Keyword"]}


def test_archive_move_failure_restores_metadata(
    et: ExifToolSession, fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = fixture("lightroom.jpg")
    m = read(et, p)

    def fail(*a: object, **k: object) -> Path:
        raise fo.FileOpError("Cannot move lightroom.jpg: disk full.")

    monkeypatch.setattr(fo, "move", fail)
    with pytest.raises(fo.FileOpError, match="were put back"):
        fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(p.parent))
    after = read(et, p)
    assert after.rating == 3 and after.subject == ["family", "Christmas", "Lightroom Keyword"]


def test_archive_rollback_failure_names_file_and_state(
    et: ExifToolSession, fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = fixture("no_date.jpg")
    m = read(et, p)
    monkeypatch.setattr(fo, "move", lambda *a, **k: (_ for _ in ()).throw(fo.FileOpError("Cannot move.")))
    real_write = et.write
    calls = {"n": 0}

    def flaky(path: Path, args: list[str]) -> str:
        calls["n"] += 1
        if calls["n"] > 1:
            raise md.MetadataError("disk error")
        return real_write(path, args)

    monkeypatch.setattr(et, "write", flaky)
    with pytest.raises(fo.FileOpError) as info:
        fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(p.parent))
    msg = str(info.value)
    assert "no_date.jpg" in msg and "NEW rating" in msg and str(p.parent) in msg


def test_archive_metadata_failure_does_not_move(
    et: ExifToolSession, fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = fixture("no_date.jpg")
    m = read(et, p)
    monkeypatch.setattr(et, "write", lambda *a: (_ for _ in ()).throw(md.MetadataError("locked")))
    with pytest.raises(fo.FileOpError, match="Could not save"):
        fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(p.parent))
    assert p.exists() and not (p.parent / "_archive").exists()


def test_keep_from_archive_with_collision(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("_archive/archived.jpg", "photos/_archive/archived.jpg")
    parent = p.parent.parent
    (parent / "ARCHIVED.jpg").write_bytes(b"other photo")
    m = read(et, p)
    res = fo.write_then_move(et, m, md.plan_keep(m), parent)
    assert res.path == parent / "archived_1.jpg"
    kept = read(et, res.path)
    assert kept.rating == 1 and "status:archive" not in kept.keywords
    assert (parent / "ARCHIVED.jpg").read_bytes() == b"other photo"


def test_undo_archive_round_trip(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("lightroom.jpg")
    folder = p.parent
    m = read(et, p)
    res = fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(folder))
    archived = read(et, res.path)
    back = fo.write_then_move(et, archived, md.restore_plan(archived, res.snapshot), folder, p.name)
    assert back.path == p
    restored = read(et, p)
    assert restored.rating == 3 and restored.subject == ["family", "Christmas", "Lightroom Keyword"]


def test_paths_with_unicode(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("Grand-mère à la plage 1962.v2.final.jpg")
    m = read(et, p)
    res = fo.write_then_move(et, m, md.plan_archive(m), fo.archive_folder(p.parent))
    assert res.path.name == p.name and read(et, res.path).rating == -1
    shutil.rmtree(res.path.parent)
