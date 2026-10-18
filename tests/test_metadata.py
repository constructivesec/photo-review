"""Metadata layer: field map, write rules, undo, and integrity (real bundled ExifTool)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import real_samples
from helpers import assert_only_changed, dump, pixels
from PIL import Image

from photo_review import metadata as md
from photo_review.metadata import ExifToolSession, PhotoMeta

Fixture = Callable[..., Path]

FORMATS = ["exif_no_xmp.jpg", "no_metadata.png", "scan.tif", "multipage.tif", "iphone.heic"]
TRICKY = [
    "-starts with a dash",
    "@starts with an at sign",
    "<angle> & $HOME",
    "has \"double\" and 'single' quotes",
    "line one\nline two\r\nline three",
    "emoji 😀👨‍👩‍👧 é ñ 日本語",
    "back\\slash and literal \\n",
    "  padded  ",
    "#hash",
    "1.50",
    "007",
]


def read(et: ExifToolSession, p: Path) -> PhotoMeta:
    return md.read_meta(et, [p])[p]


def apply(et: ExifToolSession, p: Path, plan: md.WritePlan) -> PhotoMeta:
    if not plan.empty:
        et.write(p, plan.args)
    return read(et, p)


# ---------------------------------------------------------------- arguments and reading


def test_encode_arg_plain_and_cstr() -> None:
    assert md.encode_arg("-XMP-dc:Description=hello") == "-XMP-dc:Description=hello"
    assert md.encode_arg("-T=a\nb\\c") == "#[CSTR]-T=a\\nb\\\\c"
    assert md.encode_arg("-T= x ").startswith("#[CSTR]")
    with pytest.raises(md.MetadataError):
        md.encode_arg("-T=a\x00b")
    with pytest.raises(md.MetadataError):
        md.encode_arg("-T=a\x01b")


def test_reads_lightroom_fields(et: ExifToolSession, fixture: Fixture) -> None:
    m = read(et, fixture("lightroom.jpg"))
    assert m.rating == 3
    assert m.caption == "Christmas morning"
    assert m.subject == ["family", "Christmas", "Lightroom Keyword"]
    assert m.date.value is not None and m.date.value.year == 2015
    assert m.get("IFD0:Make") == "Canon" and m.error is None


def test_reads_empty_and_missing(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    m = read(et, fixture("no_metadata.png"))
    assert m.rating is None and m.caption == "" and m.keywords == [] and m.date.undated
    missing = tmp_path / "gone.jpg"
    assert md.read_meta(et, [missing])[missing].error == "File not found"


def test_corrupt_image_metadata_is_empty_not_fatal(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("corrupt.jpg")
    good = fixture("no_date.jpg")
    metas = md.read_meta(et, [p, good])
    assert metas[good].error is None
    assert metas[p].rating is None and metas[p].caption == ""


def test_batch_read_many_files(et: ExifToolSession, fixture: Fixture) -> None:
    paths = [fixture(n) for n in ["lightroom.jpg", "june_1962.jpg", "iphone.heic", "scan.tif"]]
    metas = md.read_meta(et, paths)
    assert set(metas) == set(paths)
    assert metas[paths[1]].date.value is not None and metas[paths[1]].date.value.year == 1962


def test_gps_and_device(et: ExifToolSession, fixture: Fixture) -> None:
    m = read(et, fixture("iphone.heic"))
    assert m.get("IFD0:Make") == "Apple"
    lat, lon = m.gps or (0, 0)
    assert abs(lat - 40.7128) < 1e-4 and abs(lon + 74.006) < 1e-4


# ---------------------------------------------------------------- KEEP / ARCHIVE


@pytest.mark.parametrize("start", [None, 0, -1])
def test_keep_sets_rating_1(et: ExifToolSession, fixture: Fixture, start: int | None) -> None:
    p = fixture("no_date.jpg")
    if start is not None:
        et.write(p, [f"-XMP-xmp:Rating={start}"])
    m = apply(et, p, md.plan_keep(read(et, p)))
    assert m.rating == 1


@pytest.mark.parametrize("start", [1, 2, 3, 4, 5])
def test_keep_leaves_ratings_1_to_5(et: ExifToolSession, fixture: Fixture, start: int) -> None:
    p = fixture("no_date.jpg")
    et.write(p, [f"-XMP-xmp:Rating={start}"])
    plan = md.plan_keep(read(et, p))
    assert plan.empty  # already kept: no write at all
    assert read(et, p).rating == start


def test_archive_then_keep(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("lightroom.jpg")
    m = apply(et, p, md.plan_archive(read(et, p)))
    m = apply(et, p, md.plan_archive(m))  # twice: no duplicate keyword
    assert m.rating == -1
    assert m.subject == ["family", "Christmas", "Lightroom Keyword", "status:archive"]
    m = apply(et, p, md.plan_keep(m))
    assert m.rating == 1 and m.subject == ["family", "Christmas", "Lightroom Keyword"]


def test_keep_removes_archive_keyword_from_iptc_too(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    et.write(p, ["-XMP-xmp:Rating=-1", "-XMP-dc:Subject=status:archive", "-IPTC:Keywords=status:archive"])
    m = apply(et, p, md.plan_keep(read(et, p)))
    assert m.keywords == [] and m.rating == 1


def test_archive_keeps_ratings_out_of_exif(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("exif_no_xmp.jpg")
    apply(et, p, md.plan_archive(read(et, p)))
    assert "IFD0:Rating" not in dump(et, p)


# ---------------------------------------------------------------- captions


def test_caption_mirrors_jpeg(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("exif_no_xmp.jpg")
    m = apply(et, p, md.plan_caption(read(et, p), "Lake house", mirror=True))
    for tag in (md.DESCRIPTION, md.IMAGE_DESCRIPTION, md.IPTC_CAPTION, md.XP_COMMENT):
        assert m.get(tag) == "Lake house", tag
    assert m.iptc_is_utf8


@pytest.mark.parametrize("name", ["no_metadata.png", "scan.tif", "iphone.heic"])
def test_caption_mirrors_non_jpeg(et: ExifToolSession, fixture: Fixture, name: str) -> None:
    p = fixture(name)
    m = apply(et, p, md.plan_caption(read(et, p), "Lake house", mirror=True))
    assert m.get(md.DESCRIPTION) == "Lake house" and m.get(md.IMAGE_DESCRIPTION) == "Lake house"
    assert m.get(md.IPTC_CAPTION) is None and m.get(md.XP_COMMENT) is None


def test_caption_mirror_off_leaves_legacy_untouched(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("exif_no_xmp.jpg")
    apply(et, p, md.plan_caption(read(et, p), "old", mirror=True))
    m = apply(et, p, md.plan_caption(read(et, p), "new", mirror=False))
    assert m.caption == "new" and m.get(md.IMAGE_DESCRIPTION) == "old" and m.get(md.IPTC_CAPTION) == "old"


def test_caption_clear(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("exif_no_xmp.jpg")
    apply(et, p, md.plan_caption(read(et, p), "temp", mirror=True))
    m = apply(et, p, md.plan_caption(read(et, p), "", mirror=True))
    for tag in (md.DESCRIPTION, md.IMAGE_DESCRIPTION, md.IPTC_CAPTION, md.XP_COMMENT):
        assert m.get(tag) is None, tag


def test_long_caption_truncates_iptc_only(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("exif_no_xmp.jpg")
    text = "Grand-mère é " * 200  # > 2000 bytes
    plan = md.plan_caption(read(et, p), text, mirror=True)
    assert plan.notes
    m = apply(et, p, plan)
    assert m.caption == text
    iptc = m.get(md.IPTC_CAPTION) or ""
    assert len(iptc.encode()) <= 2000 and text.startswith(iptc)


@pytest.mark.parametrize("text", TRICKY)
def test_tricky_captions_round_trip(et: ExifToolSession, fixture: Fixture, text: str) -> None:
    p = fixture("exif_no_xmp.jpg")
    m = apply(et, p, md.plan_caption(read(et, p), text, mirror=True))
    for tag in (md.DESCRIPTION, md.IMAGE_DESCRIPTION, md.IPTC_CAPTION, md.XP_COMMENT):
        assert m.get(tag) == text, tag


def test_caption_read_precedence(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    et.write(p, ["-IPTC:Caption-Abstract=from iptc"])
    assert read(et, p).caption == "" and read(et, p).caption_placeholder == "from iptc"
    et.write(p, ["-IFD0:ImageDescription=from exif"])
    assert read(et, p).caption_placeholder == "from exif"
    et.write(p, ["-XMP-dc:Description=from xmp"])
    assert read(et, p).caption == "from xmp" and read(et, p).caption_placeholder == ""


def test_legacy_latin1_iptc_is_converted_not_corrupted(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("legacy_iptc_latin1.jpg")
    apply(et, p, md.plan_caption(read(et, p), "Grand-mère 😀", mirror=True))
    d = dump(et, p)
    assert d["IPTC:By-line"] == "Müller" and d["IPTC:City"] == "Zürich"
    assert d["IPTC:Caption-Abstract"] == "Grand-mère 😀"


# ---------------------------------------------------------------- keywords


def test_keywords_union_and_mirror(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    et.write(
        p,
        [
            "-XMP-dc:Subject=Emma",
            "-XMP-dc:Subject=Other App",
            "-IPTC:Keywords=emma",
            "-IPTC:Keywords=iptc-only",
        ],
    )
    m = read(et, p)
    assert m.keywords == ["Emma", "Other App", "iptc-only"]
    m = apply(et, p, md.plan_keywords(m, [*m.keywords, "person:zoë"]))
    assert m.subject == ["Emma", "Other App", "iptc-only", "person:zoë"]
    assert m.iptc_keywords == m.subject


def test_keywords_non_jpeg_xmp_only(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("iphone.heic")
    m = apply(et, p, md.plan_keywords(read(et, p), ["place:lake-house"]))
    assert m.subject == ["place:lake-house"] and m.iptc_keywords == []


@pytest.mark.parametrize("tag", TRICKY[:9])
def test_tricky_keywords_round_trip(et: ExifToolSession, fixture: Fixture, tag: str) -> None:
    p = fixture("exif_no_xmp.jpg")
    m = apply(et, p, md.plan_keywords(read(et, p), [tag, "second"]))
    assert m.subject == [tag, "second"]


# ---------------------------------------------------------------- dates


@pytest.mark.parametrize(
    "name", ["no_date.jpg", "no_metadata.png", "scan.tif", "multipage.tif", "iphone.heic"]
)
@pytest.mark.parametrize("value", ["1962:06:15 12:00:00", "1899:12:01 12:00:00"])
def test_pre_1970_dates_round_trip(et: ExifToolSession, fixture: Fixture, name: str, value: str) -> None:
    p = fixture(name)
    m = apply(et, p, md.plan_date(read(et, p), value, approximate=True, set_create_date=False))
    for tag in (md.EXIF_DTO, md.XMP_DTO, md.PS_DATE_CREATED):
        assert m.get(tag) == value, tag
    assert m.approximate and not m.date.mismatch


def test_date_approximate_flag_toggle(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("june_1962.jpg")
    m = read(et, p)
    assert m.approximate
    m = apply(et, p, md.plan_date(m, "1962:07:15 12:00:00", approximate=False, set_create_date=False))
    assert not m.approximate
    m = apply(et, p, md.plan_date(m, "1962:07:15 12:00:00", approximate=True, set_create_date=False))
    assert m.subject.count("date:approximate") == 1


def test_create_date_only_when_enabled(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("scanner_epson.jpg")
    m = apply(et, p, md.plan_date(read(et, p), "1962:06:15 12:00:00", False, set_create_date=False))
    assert m.get(md.EXIF_CREATE) == "2024:03:10 10:21:44" and m.get(md.XMP_CREATE) is None
    m = apply(et, p, md.plan_date(m, "1962:06:15 12:00:00", False, set_create_date=True))
    assert m.get(md.EXIF_CREATE) == "1962:06:15 12:00:00" and m.get(md.XMP_CREATE) == "1962:06:15 12:00:00"


def test_effective_date_rules(et: ExifToolSession, fixture: Fixture) -> None:
    assert read(et, fixture("zero_date.jpg")).date.undated
    mm = read(et, fixture("mismatched_dates.jpg")).date
    assert mm.mismatch and mm.value is not None and mm.value.year == 1975
    assert read(et, fixture("no_date.jpg")).date.undated


# ---------------------------------------------------------------- undo / rollback


PLANS: dict[str, Callable[[PhotoMeta], md.WritePlan]] = {
    "keep": md.plan_keep,
    "archive": md.plan_archive,
    "caption": lambda m: md.plan_caption(m, "New caption 😀", mirror=True),
    "caption_clear": lambda m: md.plan_caption(m, "", mirror=True),
    "keywords": lambda m: md.plan_keywords(m, [*m.keywords, "person:emma"]),
    "date": lambda m: md.plan_date(m, "1962:06:15 12:00:00", approximate=False, set_create_date=True),
}


@pytest.mark.parametrize(
    "name", ["lightroom.jpg", "zero_date.jpg", "june_1962.jpg", "iphone.heic", "scan.tif"]
)
@pytest.mark.parametrize("action", list(PLANS))
def test_restore_returns_every_value(et: ExifToolSession, fixture: Fixture, name: str, action: str) -> None:
    p = fixture(name)
    before_meta = read(et, p)
    before = dump(et, p)
    plan = PLANS[action](before_meta)
    if plan.empty:
        return
    snap = md.snapshot(before_meta, plan.tags)
    et.write(p, plan.args)
    after_write = read(et, p)
    et.write(p, md.restore_plan(after_write, snap).args)
    restored = dump(et, p)
    # Everything is back; only the IPTC character-set declaration may have been added.
    allowed = {"IPTC:CodedCharacterSet", "IPTC:ApplicationRecordVersion", "IPTC:EnvelopeRecordVersion"}
    assert_only_changed(before, restored, set())
    assert not set(restored) - set(before) - allowed - {"XMP-x:XMPToolkit"}


# ---------------------------------------------------------------- integrity (spec 16 round-trip)

PROTECTED = {
    "IFD0:Make",
    "IFD0:Model",
    "IFD0:Software",
    "ExifIFD:CreateDate",
    "ExifIFD:DateTimeDigitized",
    "ExifIFD:OffsetTimeOriginal",
    "ExifIFD:SubSecTimeOriginal",
    "XMP-dc:Title",
}


@pytest.mark.parametrize("name", [*FORMATS, "lightroom.jpg", "legacy_iptc_latin1.jpg"])
@pytest.mark.parametrize("action", ["keep", "archive", "caption", "keywords"])
def test_edit_changes_nothing_else(et: ExifToolSession, fixture: Fixture, name: str, action: str) -> None:
    p = fixture(name)
    m = read(et, p)
    before_px, before = pixels(p), dump(et, p)
    mtime = p.stat().st_mtime_ns
    plan = PLANS[action](m)
    if plan.empty:
        return
    et.write(p, plan.args)
    after = dump(et, p)
    assert pixels(p) == before_px
    assert p.stat().st_mtime_ns == mtime  # -P
    edited = {*plan.tags, "IPTC:CodedCharacterSet", "IPTC:ApplicationRecordVersion"}
    assert_only_changed(before, after, edited)
    assert not PROTECTED & edited


def test_date_edit_changes_nothing_else(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("iphone.heic")
    m = read(et, p)
    before_px, before = pixels(p), dump(et, p)
    plan = md.plan_date(m, "1962:06:15 12:00:00", approximate=True, set_create_date=False)
    et.write(p, plan.args)
    assert pixels(p) == before_px
    assert_only_changed(before, dump(et, p), set(plan.tags))


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="folder permissions block writes only on POSIX, and not for root",
)
def test_read_only_file_write_fails_cleanly(et: ExifToolSession, fixture: Fixture) -> None:
    p = fixture("no_date.jpg")
    p.parent.chmod(0o555)  # ExifTool replaces the file, so a read-only folder blocks the write
    try:
        before = p.read_bytes()
        with pytest.raises(md.MetadataError):
            et.write(p, md.plan_keep(read(et, p)).args)
        assert p.read_bytes() == before
    finally:
        p.parent.chmod(0o755)


def test_redact() -> None:
    assert (
        md.redact("Warning: bad 'secret text' in x.jpg", ["secret text"])
        == "Warning: bad '<redacted>' in x.jpg"
    )


@pytest.mark.skipif(not real_samples(), reason="no real samples in tests/fixtures/real")
@pytest.mark.parametrize("action", list(PLANS))
def test_real_samples(et: ExifToolSession, tmp_path: Path, action: str) -> None:
    import shutil

    for src in real_samples():
        p = Path(shutil.copy2(src, tmp_path / src.name))
        m = read(et, p)
        before_px, before = pixels(p), dump(et, p)
        plan = PLANS[action](m)
        if plan.empty:
            continue
        snap = md.snapshot(m, plan.tags)
        et.write(p, plan.args)
        assert pixels(p) == before_px, src.name
        assert_only_changed(before, dump(et, p), {*plan.tags, "IPTC:CodedCharacterSet"})
        et.write(p, md.restore_plan(read(et, p), snap).args)
        assert_only_changed(before, dump(et, p), set())
        assert Image.open(p).size
