"""Pure rules: dates (spec 9) and tags (spec 8)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from photo_review import date_logic as dl
from photo_review import tag_logic as tl

TODAY = date(2026, 10, 18)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1962:06:15 12:00:00", datetime(1962, 6, 15, 12)),
        ("2021:04:11 15:47:53-05:00", datetime(2021, 4, 11, 15, 47, 53)),
        ("2021-04-11T15:47:53.123Z", datetime(2021, 4, 11, 15, 47, 53)),
        ("1899:12:01", datetime(1899, 12, 1)),
        ("0000:00:00 00:00:00", None),
        ("", None),
        (None, None),
        ("    :  :     :  :  ", None),
        ("2021:02:30 10:00:00", None),
        ("1962", None),
        ("1962:06", None),
        ("garbage", None),
    ],
)
def test_parse(value: str | None, expected: datetime | None) -> None:
    assert dl.parse(value) == expected


def test_effective_date_precedence() -> None:
    e = dl.effective_date("1975:07:04 12:00:00", "1976:08:05 12:00:00")
    assert e.value == datetime(1975, 7, 4, 12) and e.mismatch
    e = dl.effective_date("0000:00:00 00:00:00", "1976:08:05 12:00:00")
    assert e.value == datetime(1976, 8, 5, 12) and not e.mismatch
    e = dl.effective_date("2021:04:11 15:47:53", "2021:04:11 15:47:53-05:00")
    assert not e.mismatch  # offsets ignored
    assert dl.effective_date(None, None).undated


@pytest.mark.parametrize(
    "m,d,y,ok",
    [
        ("6", "", "1962", (6, None, 1962)),
        ("06", "", "1826", (6, None, 1826)),
        ("10", "", "2026", (10, None, 2026)),
        ("11", "", "2026", None),  # future month
        ("13", "", "1962", None),
        ("0", "", "1962", None),
        ("x", "", "1962", None),
        ("6", "", "62", None),
        ("6", "", "1825", None),
        ("6", "", "2027", None),
        ("", "", "", None),
        ("6.5", "", "1962", None),
        # Day
        ("6", "15", "1962", (6, 15, 1962)),
        ("6", " 05 ", "1962", (6, 5, 1962)),
        ("6", "30", "1962", (6, 30, 1962)),
        ("6", "31", "1962", None),  # June has 30 days
        ("2", "29", "1962", None),  # not a leap year
        ("2", "29", "1964", (2, 29, 1964)),
        ("2", "29", "1900", None),  # 1900 was not a leap year
        ("6", "0", "1962", None),
        ("6", "x", "1962", None),
        ("6", "1.5", "1962", None),
        ("10", "18", "2026", (10, 18, 2026)),  # today
        ("10", "19", "2026", None),  # future day
    ],
)
def test_validate(m: str, d: str, y: str, ok: tuple[int, int | None, int] | None) -> None:
    if ok is None:
        with pytest.raises(dl.DateInputError):
            dl.validate(m, d, y, TODAY)
    else:
        assert dl.validate(m, d, y, TODAY) == ok


def test_new_value_rules() -> None:
    assert dl.new_value(None, 6, 1962) == datetime(1962, 6, 1, 12)
    assert dl.new_value(datetime(2001, 1, 31, 8, 30, 5), 2, 1962) == datetime(1962, 2, 28, 8, 30, 5)
    assert dl.new_value(datetime(2001, 1, 31, 8, 30, 5), 2, 1964) == datetime(1964, 2, 29, 8, 30, 5)
    # With a day: that exact day, keeping the time of day (noon if there was no date)
    assert dl.new_value(None, 6, 1962, 15) == datetime(1962, 6, 15, 12)
    assert dl.new_value(datetime(2001, 1, 31, 8, 30, 5), 2, 1962, 14) == datetime(1962, 2, 14, 8, 30, 5)
    assert dl.format_exif(datetime(1899, 12, 1, 12)) == "1899:12:01 12:00:00"
    assert dl.format_display(datetime(1962, 6, 15, 12)) == "1962-06-15 12:00:00"


def test_csv_date() -> None:
    assert dl.csv_date(datetime(1962, 6, 15), approximate=True) == "1962-06"
    assert dl.csv_date(datetime(1962, 6, 15), approximate=False) == "1962-06-15"
    assert dl.csv_date(None, False) == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Emma", "emma"),
        ("person:Emma Rose", "person:emma-rose"),
        ("event:christmas-2025", "event:christmas-2025"),
        ("place:Zürich", "place:zürich"),
        ("  lake   house ", "lake-house"),
        ("pet:rex", "pet:rex"),
    ],
)
def test_normalize_ok(text: str, expected: str) -> None:
    assert tl.normalize(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a,b",
        "a;b",
        "a:b:c",
        ":emma",
        "person:",
        "status:archive",
        "date:1962",
        "Status:x",
        "emma!",
        "under_score",
        "x" * 65,
        "é" * 33,
    ],
)
def test_normalize_rejects(text: str) -> None:
    with pytest.raises(tl.TagError):
        tl.normalize(text)


def test_user_system_and_foreign_tags() -> None:
    assert tl.is_user_tag("person:emma")
    assert not tl.is_user_tag("status:archive") and tl.is_system("status:archive")
    assert not tl.is_user_tag("Lightroom Keyword")  # foreign: read-only
    with pytest.raises(tl.TagError):
        tl.remove(["Lightroom Keyword"], "Lightroom Keyword")
    assert tl.remove(["a", "person:emma"], "person:emma") == ["a"]


def test_merge_dedupe_prefers_xmp() -> None:
    assert tl.merge_keywords(["Emma", "x"], ["emma", "y", "X"]) == ["Emma", "x", "y"]
    assert tl.add(["Emma"], "emma") == ["Emma"]


def test_history_and_suggestions() -> None:
    h = tl.update_history(["old"], ["person:emma", "Foreign Tag"])
    assert h == ["person:emma", "old"]
    assert len(tl.update_history([f"t{i}" for i in range(600)], ["new"])) == 500
    assert tl.suggestions("em", ["person:emma", "Foreign"], ["event:emmy"]) == ["person:emma", "event:emmy"]


def test_scanner_terms() -> None:
    from photo_review.ui.model import looks_like_scanner

    assert looks_like_scanner("EPSON", "Perfection V600")
    assert looks_like_scanner("MAKER NAME", "96660")  # owner's Novatek slide scanner (Q23)
    assert looks_like_scanner("", "Novatek NT96660")
    assert looks_like_scanner("Canon", "CanoScan 9000F")
    assert not looks_like_scanner("Apple", "iPhone 11")
    assert not looks_like_scanner("Canon", "Canon EOS 40D")


def test_camera_placeholders() -> None:
    from photo_review.metadata import is_camera_placeholder

    for text in ["NOVATEK CAMERA", "OLYMPUS DIGITAL CAMERA", "  sony   dsc ", "EXIF_JPEG_PICTURE"]:
        assert is_camera_placeholder(text), text
    assert not is_camera_placeholder("Novatek camera club picnic")
