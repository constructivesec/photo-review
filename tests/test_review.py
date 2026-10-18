"""Review model + actions: list rules, views, filters, sorting and undo for every action type."""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from photo_review import date_logic, tag_logic
from photo_review import metadata as md
from photo_review.config import Config
from photo_review.ui import actions
from photo_review.ui.model import ReviewModel, order_paths

TODAY = date(2026, 10, 18)


@pytest.fixture
def folder(fixture_dir: Path, tmp_path: Path) -> Path:
    """A small review folder with known dates and ratings."""
    f = tmp_path / "photos"
    f.mkdir()
    for n in [
        "lightroom.jpg",
        "june_1962.jpg",
        "no_date.jpg",
        "december_1899.jpg",
        "iphone.heic",
        "zero_date.jpg",
    ]:
        shutil.copy2(fixture_dir / n, f / n)
    return f


def load(et: md.ExifToolSession, folder: Path, sort_key: str = "name_asc", **kw: bool) -> ReviewModel:
    m = ReviewModel(folder=folder, sort_key=sort_key, **kw)
    stamps = actions.scan(m.view_folder)  # type: ignore[arg-type]
    m.set_listing(stamps, None)
    m.store_metas(actions.read_batch(et, m.missing_metadata()))
    m.rebuild(None)
    return m


def names(m: ReviewModel) -> list[str]:
    return [p.name for p in m.order]


def cur(et: md.ExifToolSession, m: ReviewModel) -> md.PhotoMeta:
    assert m.current is not None
    return m.meta(m.current) or actions.reread(et, m.current)


# ---------------------------------------------------------------- sorting and filtering


def test_sorts(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder, "date")
    assert names(m) == [
        "december_1899.jpg",
        "june_1962.jpg",
        "lightroom.jpg",
        "iphone.heic",
        "no_date.jpg",
        "zero_date.jpg",
    ]  # undated last, by filename
    m.sort_key = "name_desc"
    m.rebuild(None)
    assert names(m)[0] == "zero_date.jpg"
    m.sort_key = "rating"
    m.rebuild(None)
    assert names(m)[0] == "lightroom.jpg"  # rating 3 first, then filename order


def test_sort_stability_and_keep_current(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder, "name_asc")
    m.go_to(folder / "june_1962.jpg")
    m.sort_key = "date"
    m.rebuild(m.current)
    assert m.current == folder / "june_1962.jpg"
    first = list(m.order)
    m.rebuild(m.current)
    assert m.order == first


def test_modified_sorts_use_mtime(et: md.ExifToolSession, folder: Path) -> None:
    for i, n in enumerate(sorted(os.listdir(folder))):
        os.utime(folder / n, (1_000_000_000 + i, 1_000_000_000 + i))
    m = load(et, folder, "modified_desc")
    assert names(m)[0] == sorted(os.listdir(folder))[-1]


def test_undated_filter_and_date_save_leaves_list(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder, "name_asc", undated_only=True)
    assert names(m) == ["no_date.jpg", "zero_date.jpg"] and m.total == 2
    meta = cur(et, m)
    r = actions.save_date(et, meta, "6", "1962", approximate=True, set_create_date=False, today=TODAY)
    assert m.applied_edit("date", r.path, r.snapshot or {}, r.meta)
    assert names(m) == ["zero_date.jpg"] and m.total == 1 and m.current == folder / "zero_date.jpg"
    # Undo restores it to the filtered list at its position
    entry = m.undo[-1]
    u = actions.undo(et, entry)
    m.applied_undo(entry, u.path, u.meta)
    assert names(m) == ["no_date.jpg", "zero_date.jpg"] and m.current == folder / "no_date.jpg"
    assert u.meta.date.undated and not u.meta.approximate


def test_edits_never_reorder(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder, "date")
    before = list(m.order)
    m.go_to(folder / "no_date.jpg")
    r = actions.save_date(et, cur(et, m), "1", "1850", True, False, TODAY)
    m.applied_edit("date", r.path, r.snapshot or {}, r.meta)
    assert m.order == before


# ---------------------------------------------------------------- KEEP / ARCHIVE


def test_keep_advances_and_total_unchanged(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    first = m.current
    r = actions.keep(et, cur(et, m), False, folder)
    assert not m.applied_keep(r.old_path, r.path, r.snapshot, r.meta)
    assert m.total == 6 and m.position == 2 and r.meta.rating == 1 and first is not None


def test_keep_already_kept_is_noop_that_advances(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "lightroom.jpg")
    before = (folder / "lightroom.jpg").read_bytes()
    r = actions.keep(et, cur(et, m), False, folder)
    assert r.snapshot is None and (folder / "lightroom.jpg").read_bytes() == before
    m.applied_keep(r.old_path, r.path, r.snapshot, r.meta)
    assert m.undo == [] and m.current != folder / "lightroom.jpg"


def test_keep_on_last_photo_is_finished(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.index = m.total - 1
    r = actions.keep(et, cur(et, m), False, folder)
    assert m.applied_keep(r.old_path, r.path, r.snapshot, r.meta)


def test_archive_removes_and_undo_restores_position(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "june_1962.jpg")
    pos = m.index
    r = actions.archive(et, cur(et, m), folder)
    assert r.path == folder / "_archive" / "june_1962.jpg"
    m.applied_archive(r.old_path, r.path, r.snapshot or {})
    assert m.total == 5 and m.index == pos and "june_1962.jpg" not in names(m)
    entry = m.undo[-1]
    u = actions.undo(et, entry)
    m.applied_undo(entry, u.path, u.meta)
    assert u.path == folder / "june_1962.jpg" and m.index == pos and m.total == 6
    assert u.meta.rating is None and u.meta.subject == ["date:approximate"]


def test_archive_last_shows_previous(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.index = m.total - 1
    r = actions.archive(et, cur(et, m), folder)
    assert m.applied_archive(r.old_path, r.path, r.snapshot or {})
    assert m.position == m.total == 5


def test_archive_collision_case_insensitive(et: md.ExifToolSession, folder: Path) -> None:
    (folder / "_archive").mkdir()
    (folder / "_archive" / "NO_DATE.JPG").write_bytes(b"older archived photo")
    m = load(et, folder)
    m.go_to(folder / "no_date.jpg")
    r = actions.archive(et, cur(et, m), folder)
    assert r.path.name == "no_date_1.jpg"


def test_archived_view_keep_moves_to_parent_and_undo(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "lightroom.jpg")
    r = actions.archive(et, cur(et, m), folder)
    m.applied_archive(r.old_path, r.path, r.snapshot or {})
    a = load(et, folder, archived_view=True)
    assert names(a) == ["lightroom.jpg"]
    k = actions.keep(et, cur(et, a), True, folder)
    assert k.path == folder / "lightroom.jpg" and k.meta.rating == 1
    assert "status:archive" not in k.meta.keywords
    assert a.applied_keep(k.old_path, k.path, k.snapshot, k.meta) and a.total == 0
    entry = a.undo[-1]
    u = actions.undo(et, entry)
    a.applied_undo(entry, u.path, u.meta)
    assert u.path == folder / "_archive" / "lightroom.jpg" and u.meta.rating == -1 and a.total == 1


# ---------------------------------------------------------------- caption, tags, date undo


def test_caption_save_and_undo(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "lightroom.jpg")
    r = actions.save_caption(et, cur(et, m), "New caption", mirror=True)
    m.applied_edit("caption", r.path, r.snapshot or {}, r.meta)
    assert r.meta.caption == "New caption" and r.meta.get(md.IPTC_CAPTION) == "New caption"
    entry = m.undo[-1]
    u = actions.undo(et, entry)
    m.applied_undo(entry, u.path, u.meta)
    assert u.meta.caption == "Christmas morning" and u.meta.get(md.IPTC_CAPTION) is None


def test_tags_add_remove_and_undo(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "lightroom.jpg")
    r = actions.add_tag(et, cur(et, m), "Person:Emma Rose")
    m.applied_edit("tags", r.path, r.snapshot or {}, r.meta)
    assert r.meta.keywords == ["family", "Christmas", "Lightroom Keyword", "person:emma-rose"]
    assert r.meta.iptc_keywords == r.meta.keywords
    with pytest.raises(tag_logic.TagError):
        actions.add_tag(et, r.meta, "status:archive")
    with pytest.raises(tag_logic.TagError):
        actions.remove_tag(et, r.meta, "Lightroom Keyword")
    r2 = actions.remove_tag(et, r.meta, "person:emma-rose")
    m.applied_edit("tags", r2.path, r2.snapshot or {}, r2.meta)
    for _ in range(2):
        entry = m.undo[-1]
        u = actions.undo(et, entry)
        m.applied_undo(entry, u.path, u.meta)
    assert u.meta.keywords == ["family", "Christmas", "Lightroom Keyword"] and u.meta.iptc_keywords == []


def test_date_edit_rules_and_undo(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "lightroom.jpg")  # 2015-12-25 09:15:00
    with pytest.raises(date_logic.DateInputError):
        actions.save_date(et, cur(et, m), "2", "abc", False, False, TODAY)
    r = actions.save_date(et, cur(et, m), "2", "1962", approximate=False, set_create_date=False, today=TODAY)
    assert r.meta.get(md.EXIF_DTO) == "1962:02:25 09:15:00"
    assert r.meta.get(md.PS_DATE_CREATED) == "1962:02:25 09:15:00"
    m.applied_edit("date", r.path, r.snapshot or {}, r.meta)
    entry = m.undo[-1]
    u = actions.undo(et, entry)
    m.applied_undo(entry, u.path, u.meta)
    assert u.meta.get(md.EXIF_DTO) == "2015:12:25 09:15:00"
    assert u.meta.get(md.PS_DATE_CREATED) == "2015:12:25 09:15:00"


def test_undo_restores_zero_date(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    m.go_to(folder / "zero_date.jpg")
    r = actions.save_date(et, cur(et, m), "6", "1962", True, False, TODAY)
    m.applied_edit("date", r.path, r.snapshot or {}, r.meta)
    u = actions.undo(et, m.undo[-1])
    assert u.meta.get(md.EXIF_DTO) == "0000:00:00 00:00:00" and u.meta.date.undated


def test_stale_file_cancels_action(et: md.ExifToolSession, folder: Path) -> None:
    m = load(et, folder)
    meta = cur(et, m)
    os.utime(meta.path, ns=(0, meta.path.stat().st_mtime_ns + 10**10))
    from photo_review.file_ops import StaleFileError

    with pytest.raises(StaleFileError):
        actions.keep(et, meta, False, folder)


def test_order_paths_handles_missing_meta(folder: Path) -> None:
    paths = list(folder.iterdir())
    assert len(order_paths(paths, {}, {}, "date", False)) == len(paths)


# ---------------------------------------------------------------- config


def test_config_round_trip_and_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    c = Config(last_folder="/x", sort_key="date", tag_history=["a"] * 3, caption_bar_percent=99)
    c.save(p)
    loaded = Config.load(p)
    assert loaded.sort_key == "date" and loaded.caption_bar_percent == 10 and loaded.schema_version == 1
    p.write_text("{broken", encoding="utf-8")
    assert Config.load(p) == Config()
    assert (tmp_path / "config.bad").exists() and not p.exists()
    p.write_text('{"show_gps": "yes"}', encoding="utf-8")
    assert Config.load(p) == Config()
