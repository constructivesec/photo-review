import os
from pathlib import Path

import pytest
from PIL import Image

from photo_review import imaging
from photo_review.fileops import FileOpError
from photo_review.session import ReviewSession, sort_paths

from conftest import make_image


def names(paths):
    return [p.name for p in paths]


@pytest.fixture
def session(photo_dir, keep_dir):
    for n in ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]:
        make_image(photo_dir / n)
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    yield s
    s.close()


# ---------------------------------------------------------------- NO

def test_no_renames_and_advances(session, photo_dir):
    r = session.mark_no()
    assert (photo_dir / "a_NO.jpg").exists() and not (photo_dir / "a.jpg").exists()
    assert names(session.visible) == ["a_NO.jpg", "b.jpg", "c.jpg", "d.jpg"]
    assert session.current.name == "b.jpg" and session.total == 4
    assert not r.finished


def test_no_on_already_no_does_nothing(photo_dir, keep_dir):
    make_image(photo_dir / "x_NO.jpg")
    make_image(photo_dir / "y.jpg")
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    s.mark_no()
    assert (photo_dir / "x_NO.jpg").exists()
    assert not s.undo_stack
    assert s.current.name == "y.jpg"


def test_no_collision_suffix_before_no(photo_dir, keep_dir):
    make_image(photo_dir / "a.jpg")
    make_image(photo_dir / "a_NO.jpg")
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    assert s.current.name == "a.jpg"
    s.mark_no()
    assert (photo_dir / "a_1_NO.jpg").exists()


def test_no_with_hide_removes_from_visible(session):
    session.set_hide_no(True)
    session.next()  # on b
    session.mark_no()
    assert names(session.visible) == ["a.jpg", "c.jpg", "d.jpg"]
    assert session.current.name == "c.jpg" and session.position == 2


def test_no_on_last_photo_is_finished(session):
    for _ in range(3):
        session.next()
    r = session.mark_no()
    assert r.finished and session.current.name == "d_NO.jpg"


# ---------------------------------------------------------------- YES

def test_yes_moves_to_keep_and_shows_next(session, photo_dir, keep_dir):
    r = session.mark_yes()
    assert (keep_dir / "a.jpg").exists() and not (photo_dir / "a.jpg").exists()
    assert session.total == 3 and session.current.name == "b.jpg"
    assert "a.jpg" in r.message


def test_yes_on_last_shows_previous(session, keep_dir):
    for _ in range(3):
        session.next()
    r = session.mark_yes()
    assert r.finished and session.current.name == "c.jpg" and session.position == 3


def test_yes_all_photos(session):
    for _ in range(4):
        r = session.mark_yes()
    assert r.finished and session.current is None and session.total == 0


def test_yes_strips_no(session, keep_dir):
    session.mark_no()
    session.back()
    assert session.current.name == "a_NO.jpg"
    session.mark_yes()
    assert (keep_dir / "a.jpg").exists()


def test_yes_collision_in_keep(session, keep_dir):
    make_image(keep_dir / "a.jpg")
    make_image(keep_dir / "a_1.jpg")
    session.mark_yes()
    assert (keep_dir / "a_2.jpg").exists()


def test_yes_moves_captioned_copy(session, photo_dir, keep_dir):
    session.add_caption("hi")
    assert (photo_dir / "a_captioned.jpg").exists()
    session.mark_yes()
    assert (keep_dir / "a.jpg").exists() and (keep_dir / "a_captioned.jpg").exists()
    assert not (photo_dir / "a_captioned.jpg").exists()


def test_yes_blocked_when_keep_is_source(session, photo_dir):
    session.keep_folder = photo_dir
    with pytest.raises(FileOpError, match="same"):
        session.mark_yes()
    assert session.total == 4 and (photo_dir / "a.jpg").exists()


def test_yes_failure_keeps_state(session, photo_dir, monkeypatch):
    def boom(a, b):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(FileOpError):
        session.mark_yes()
    assert session.total == 4 and session.current.name == "a.jpg" and not session.undo_stack


# ---------------------------------------------------------------- undo

def test_undo_yes_restores_file_and_position(session, photo_dir, keep_dir):
    session.next()  # b
    session.add_caption("cap")
    session.mark_yes()
    session.undo_stack.pop(-2)  # leave only the YES for this test
    session.undo()
    assert (photo_dir / "b.jpg").exists() and not (keep_dir / "b.jpg").exists()
    assert (photo_dir / "b_captioned.jpg").exists() and not (keep_dir / "b_captioned.jpg").exists()
    assert names(session.visible) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    assert session.current.name == "b.jpg"


def test_undo_yes_restores_no_name(session, photo_dir, keep_dir):
    session.mark_no()
    session.back()
    session.mark_yes()
    assert (keep_dir / "a.jpg").exists()
    session.undo()
    assert (photo_dir / "a_NO.jpg").exists()
    assert session.current.name == "a_NO.jpg"


def test_undo_no(session, photo_dir):
    session.mark_no()
    session.undo()
    assert (photo_dir / "a.jpg").exists() and not (photo_dir / "a_NO.jpg").exists()
    assert session.current.name == "a.jpg"


def test_undo_caption_new_copy_deleted(session, photo_dir):
    session.add_caption("one")
    session.undo()
    assert not (photo_dir / "a_captioned.jpg").exists()
    assert (photo_dir / "a.jpg").exists()


def test_undo_caption_restores_previous(session, photo_dir):
    session.add_caption("one")
    first = (photo_dir / "a_captioned.jpg").read_bytes()
    session.add_caption("a different, longer caption")
    assert (photo_dir / "a_captioned.jpg").read_bytes() != first
    session.undo()
    assert (photo_dir / "a_captioned.jpg").read_bytes() == first


def test_undo_is_unlimited_and_ordered(session, photo_dir, keep_dir):
    session.mark_no()          # a -> a_NO, on b
    session.mark_yes()         # b kept, on c
    session.add_caption("c!")  # c captioned
    session.undo(); session.undo(); session.undo()
    assert sorted(p.name for p in photo_dir.iterdir()) == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    assert not keep_dir.exists() or not any(keep_dir.iterdir())
    with pytest.raises(FileOpError):
        session.undo()


def test_undo_cleared_on_reload(session):
    session.mark_no()
    session.load()
    assert not session.can_undo()


# ---------------------------------------------------------------- sorting / filtering

def test_hide_no_filter_and_toggle_keeps_current(photo_dir, keep_dir):
    for n in ["a.jpg", "b_NO.jpg", "c.jpg"]:
        make_image(photo_dir / n)
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    s.go_to(photo_dir / "c.jpg")
    s.set_hide_no(True)
    assert names(s.visible) == ["a.jpg", "c.jpg"] and s.current.name == "c.jpg" and s.total == 2
    s.set_hide_no(False)
    assert s.total == 3 and s.current.name == "c.jpg"


def test_hide_no_when_current_is_hidden_moves_to_next(photo_dir, keep_dir):
    for n in ["a.jpg", "b_NO.jpg", "c.jpg"]:
        make_image(photo_dir / n)
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    s.next()
    s.set_hide_no(True)
    assert s.current.name == "c.jpg"


def test_sort_by_name_and_keep_current(session):
    session.next()  # b
    session.set_sort("name_desc")
    assert names(session.visible) == ["d.jpg", "c.jpg", "b.jpg", "a.jpg"]
    assert session.current.name == "b.jpg"


def test_sort_by_modified(photo_dir, keep_dir):
    for i, n in enumerate(["x.jpg", "y.jpg", "z.jpg"]):
        p = make_image(photo_dir / n)
        t = 1_000_000_000 + (3 - i) * 100
        os.utime(p, (t, t))
    s = ReviewSession(photo_dir, keep_dir, sort_key="modified_asc")
    s.load()
    assert names(s.visible) == ["z.jpg", "y.jpg", "x.jpg"]
    s.set_sort("modified_desc")
    assert names(s.visible) == ["x.jpg", "y.jpg", "z.jpg"]


def test_sort_by_date_taken_falls_back_to_mtime(photo_dir, keep_dir):
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2000:01:01 00:00:00"
    old = make_image(photo_dir / "z_old.jpg", exif=exif.tobytes())
    new = make_image(photo_dir / "a_new.png")
    os.utime(new, (1_500_000_000, 1_500_000_000))
    os.utime(old, (1_600_000_000, 1_600_000_000))  # mtime newer, EXIF older
    s = ReviewSession(photo_dir, keep_dir, sort_key="taken_asc")
    s.load()
    assert names(s.visible) == ["z_old.jpg", "a_new.png"]


def test_sort_paths_natural_order():
    paths = [Path(n) for n in ["IMG_10.jpg", "img_2.jpg", "IMG_1.jpg"]]
    assert names(sort_paths(paths, "name_asc", lambda p: 0)) == ["IMG_1.jpg", "img_2.jpg", "IMG_10.jpg"]


def test_navigation_bounds(session):
    assert not session.can_back()
    session.back()
    assert session.position == 1
    for _ in range(10):
        session.next()
    assert session.position == 4 and not session.can_next()


def test_empty_folder(photo_dir, keep_dir):
    s = ReviewSession(photo_dir, keep_dir)
    s.load()
    assert s.current is None and s.total == 0 and s.position == 0
    with pytest.raises(FileOpError):
        s.mark_yes()
