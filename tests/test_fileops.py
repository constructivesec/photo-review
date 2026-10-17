import errno
import os
from pathlib import Path

import pytest

from photo_review import fileops
from photo_review.fileops import FileOpError

from conftest import make_image


def test_no_and_base_stem():
    assert fileops.is_no(Path("IMG_001_NO.jpg"))
    assert not fileops.is_no(Path("IMG_001.jpg"))
    assert not fileops.is_no(Path("NOTES.jpg"))
    assert fileops.base_stem(Path("IMG_001_NO.jpg")) == "IMG_001"
    assert fileops.base_stem(Path("a.b.c_NO.jpeg")) == "a.b.c"


def test_captioned_path_rules():
    assert fileops.captioned_path(Path("/x/IMG.jpg")) == Path("/x/IMG_captioned.jpg")
    assert fileops.captioned_path(Path("/x/IMG_NO.JPEG")) == Path("/x/IMG_captioned.JPEG")
    assert fileops.captioned_path(Path("/x/IMG.HEIC")) == Path("/x/IMG_captioned.jpg")
    assert fileops.captioned_path(Path("/x/IMG.png")) == Path("/x/IMG_captioned.png")


def test_supported_filter():
    for name in ["a.jpg", "a.JPG", "a.jpeg", "a.png", "a.heic", "a.HEIF"]:
        assert fileops.is_supported(Path(name)), name
    for name in ["a.mov", "a.mp4", "a.gif", "a.txt", "a_captioned.jpg", "a_captioned_NO.jpg"]:
        assert not fileops.is_supported(Path(name)), name


def test_scan_folder_not_recursive_and_filters(photo_dir):
    make_image(photo_dir / "IMG_10.jpg")
    make_image(photo_dir / "IMG_2.JPG", fmt="JPEG")
    make_image(photo_dir / "my photo.v2.final.png")
    make_image(photo_dir / "fête 日本.jpeg", fmt="JPEG")
    make_image(photo_dir / "IMG_2_captioned.jpg")
    (photo_dir / "IMG_2.mov").write_bytes(b"x")
    (photo_dir / "notes.txt").write_text("x")
    make_image(photo_dir / "sub" / "deep.jpg")
    names = [p.name for p in fileops.scan_folder(photo_dir)]
    assert names == ["fête 日本.jpeg", "IMG_2.JPG", "IMG_10.jpg", "my photo.v2.final.png"]


def test_no_target_and_collision(photo_dir):
    src = make_image(photo_dir / "IMG_001.jpg")
    assert fileops.no_target(src).name == "IMG_001_NO.jpg"
    make_image(photo_dir / "IMG_001_NO.jpg")
    assert fileops.no_target(src).name == "IMG_001_1_NO.jpg"
    make_image(photo_dir / "IMG_001_1_NO.jpg")
    assert fileops.no_target(src).name == "IMG_001_2_NO.jpg"


def test_keep_target_strips_no_and_handles_collision(photo_dir, keep_dir):
    keep_dir.mkdir()
    src = make_image(photo_dir / "IMG_5_NO.jpg")
    assert fileops.keep_target(src, keep_dir).name == "IMG_5.jpg"
    make_image(keep_dir / "IMG_5.jpg")
    assert fileops.keep_target(src, keep_dir).name == "IMG_5_1.jpg"
    make_image(keep_dir / "IMG_5_1.jpg")
    assert fileops.keep_target(src, keep_dir).name == "IMG_5_2.jpg"


def test_move_never_overwrites(photo_dir):
    a = make_image(photo_dir / "a.jpg", color=(1, 2, 3))
    b = make_image(photo_dir / "b.jpg", color=(4, 5, 6))
    before = b.read_bytes()
    with pytest.raises(FileOpError):
        fileops.move_file(a, b)
    assert a.exists() and b.read_bytes() == before


def test_move_across_volumes_copies_and_verifies(photo_dir, keep_dir, monkeypatch):
    keep_dir.mkdir()
    src = make_image(photo_dir / "x.jpg")
    data = src.read_bytes()

    def fake_rename(a, b):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", fake_rename)
    dst = keep_dir / "x.jpg"
    fileops.move_file(src, dst)
    assert not src.exists()
    assert dst.read_bytes() == data


def test_move_across_volumes_failed_verification_keeps_original(photo_dir, keep_dir, monkeypatch):
    keep_dir.mkdir()
    src = make_image(photo_dir / "x.jpg")
    monkeypatch.setattr(os, "rename", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
    monkeypatch.setattr(fileops, "_sha256", lambda p: str(p))  # force mismatch
    with pytest.raises(FileOpError):
        fileops.move_file(src, keep_dir / "x.jpg")
    assert src.exists()
    assert not (keep_dir / "x.jpg").exists()


def test_move_permission_error_is_reported(photo_dir, keep_dir, monkeypatch):
    src = make_image(photo_dir / "x.jpg")
    monkeypatch.setattr(os, "rename", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "denied")))
    with pytest.raises(FileOpError, match="permission denied"):
        fileops.move_file(src, photo_dir / "y.jpg")
    assert src.exists()


def test_validate_keep_folder(photo_dir, tmp_path):
    assert fileops.validate_keep_folder(None, photo_dir)
    assert fileops.validate_keep_folder(Path("relative/keep"), photo_dir)
    assert "same" in fileops.validate_keep_folder(photo_dir, photo_dir)
    assert fileops.validate_keep_folder(tmp_path / "new" / "keep", photo_dir) is None
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert fileops.validate_keep_folder(f, photo_dir)
