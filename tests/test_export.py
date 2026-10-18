"""Export for photo book (spec section 10)."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import real_samples
from PIL import Image, ImageCms

from photo_review import export as ex
from photo_review import metadata as md
from photo_review.metadata import ExifToolSession

Fixture = Callable[..., Path]


def metas(et: ExifToolSession, paths: list[Path]) -> list[md.PhotoMeta]:
    got = md.read_meta(et, paths)
    return [got[p] for p in paths]


# ---------------------------------------------------------------- bar formula and text fit


@pytest.mark.parametrize(
    "height,percent,expected",
    [
        (600, 5, 50),  # 30 -> minimum 50
        (2000, 5, 100),
        (1010, 5, 51),  # 50.5 rounds half up
        (1009, 5, 50),
        (3000, 5, 150),
        (8000, 5, 300),  # 400 -> maximum 300
        (2000, 10, 200),
        (2000, 2, 50),  # 40 -> minimum
        (2000, 1, 50),  # percent clamped to 2
        (2000, 50, 200),  # percent clamped to 10
    ],
)
def test_bar_height(height: int, percent: int, expected: int) -> None:
    assert ex.bar_height(height, percent) == expected
    assert ex.bar_height(height, percent) == ex.bar_height(height, percent)  # deterministic


def test_fit_caption_shrinks_then_truncates() -> None:
    font, text, cut = ex.fit_caption("Short", 2000, 100)
    assert font.size == 60 and text == "Short" and not cut
    long = "A long caption about the lake house " * 4
    font, text, cut = ex.fit_caption(long, 1200, 100)
    assert 12 <= font.size < 60 and not cut and font.getlength(text) <= 1200 - 2 * 24
    font, text, cut = ex.fit_caption(long * 5, 400, 50)
    assert font.size == 12 and cut and text.endswith("…") and font.getlength(text) <= 400 - 2 * 8


def white_box(im: Image.Image, top: int) -> tuple[int, int, int, int]:
    px = im.convert("RGB").load()
    xs, ys = [], []
    for y in range(top, im.height):
        for x in range(im.width):
            if min(px[x, y]) > 200:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize("text", ["Lake", "gypsy jig QTy", "Émile ÅÑ"])
def test_burned_text_is_centered(text: str) -> None:
    im = Image.new("RGB", (800, 1200), (0, 0, 255))
    out, cut = ex.burn_caption(im, text, 5)
    bh = ex.bar_height(1200, 5)
    assert out.size == (800, 1200 + bh) and not cut
    x0, y0, x1, y1 = white_box(out, 1200)
    assert abs((x0 + x1) / 2 - 400) <= 2
    assert abs((y0 + y1) / 2 - (1200 + bh / 2)) <= 2  # centered by rendered bounding box
    assert out.getpixel((2, 1200 + 1)) == (0, 0, 0)


# ---------------------------------------------------------------- export


def run(et: ExifToolSession, paths: list[Path], out: Path, **opts: object) -> ex.ExportSummary:
    return ex.export_photos(et, metas(et, paths), out, ex.ExportOptions(**opts))  # type: ignore[arg-type]


def test_export_names_formats_and_csv(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    paths = [
        fixture(n) for n in ["june_1962.jpg", "iphone.heic", "scan.tif", "no_metadata.png", "lightroom.jpg"]
    ]
    out = tmp_path / "out"
    s = run(et, paths, out)
    assert s.exported == [
        "0001_june_1962.jpg",
        "0002_iphone.jpg",
        "0003_scan.jpg",
        "0004_no_metadata.png",
        "0005_lightroom.jpg",
    ]
    assert s.converted == ["0002_iphone.jpg", "0003_scan.jpg"] and not s.skipped
    assert Image.open(out / "0002_iphone.jpg").format == "JPEG"
    assert Image.open(out / "0004_no_metadata.png").format == "PNG"
    raw = (out / "captions.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0] == ["filename", "date_taken", "caption", "tags"]
    assert rows[1] == ["0001_june_1962.jpg", "1962-06", "", ""]  # approximate: YYYY-MM; date: tag hidden
    assert rows[5] == [
        "0005_lightroom.jpg",
        "2015-12-25",
        "Christmas morning",
        "family; Christmas; Lightroom Keyword",
    ]


def test_unchanged_jpeg_and_png_are_byte_copies(
    et: ExifToolSession, fixture: Fixture, tmp_path: Path
) -> None:
    paths = [fixture("lightroom.jpg"), fixture("no_metadata.png")]
    out = tmp_path / "out"
    run(et, paths, out)
    assert (out / "0001_lightroom.jpg").read_bytes() == paths[0].read_bytes()
    assert (out / "0002_no_metadata.png").read_bytes() == paths[1].read_bytes()


def test_rotated_jpeg_is_made_upright(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    p = fixture("rotated_exif6.jpg")
    out = tmp_path / "out"
    run(et, [p], out)
    dst = out / "0001_rotated_exif6.jpg"
    stored = Image.open(p).size
    with Image.open(dst) as im:
        assert im.size == (stored[1], stored[0])
        assert im.getexif().get(0x0112) == 1
    m = md.read_meta(et, [dst])[dst]
    assert m.caption == "Rotated"


def test_converted_file_keeps_metadata(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    p = fixture("iphone.heic")
    et.write(p, md.plan_caption(md.read_meta(et, [p])[p], "Fireworks", mirror=True).args)
    et.write(p, ["-XMP-xmp:Rating=1", "-XMP-dc:Subject=place:nyc"])
    out = tmp_path / "out"
    run(et, [p], out)
    dst = out / "0001_iphone.jpg"
    m = md.read_meta(et, [dst])[dst]
    assert m.caption == "Fireworks" and m.rating == 1 and m.subject == ["place:nyc"]
    assert m.get("IFD0:Make") == "Apple" and m.get(md.EXIF_DTO) == "2024:07:04 18:30:12"
    assert ex.is_srgb_or_untagged(Image.open(dst))


def test_burned_caption_export(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    p, q = fixture("lightroom.jpg"), fixture("no_date.jpg")
    out = tmp_path / "out"
    s = run(et, [p, q], out, burn_caption=True, bar_percent=10)
    h = Image.open(p).height
    assert Image.open(out / "0001_lightroom.jpg").height == h + ex.bar_height(h, 10)
    assert Image.open(out / "0002_no_date.jpg").height == Image.open(q).height  # empty caption: no bar
    assert s.burned == ["0001_lightroom.jpg"]
    assert (out / "0002_no_date.jpg").read_bytes() == q.read_bytes()
    assert p.read_bytes() == fixture("lightroom.jpg", "again.jpg").read_bytes()  # original untouched


def test_truncated_caption_reported(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    p = fixture("no_date.jpg")
    et.write(p, ["-XMP-dc:Description=" + "very long caption " * 40])
    s = run(et, [p], tmp_path / "out", burn_caption=True)
    assert s.truncated == ["0001_no_date.jpg"]


def test_prefix_off_collisions_get_suffix(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    a = fixture("iphone.heic", "IMG_1.heic")
    b = fixture("no_date.jpg", "IMG_1.jpg")
    s = run(et, [a, b], tmp_path / "out", sequence_prefix=False)
    assert s.exported == ["IMG_1.jpg", "IMG_1_1.jpg"] and s.renamed == ["IMG_1.jpg -> IMG_1_1.jpg"]


def test_non_empty_folder_refused(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.jpg").write_bytes(b"keep me")
    with pytest.raises(ex.ExportFolderNotEmpty):
        run(et, [fixture("no_date.jpg")], out)
    assert [f.name for f in out.iterdir()] == ["existing.jpg"]


def test_corrupt_and_archived_are_skipped(et: ExifToolSession, fixture: Fixture, tmp_path: Path) -> None:
    good, bad = fixture("no_date.jpg"), fixture("corrupt.jpg")
    arch = fixture("_archive/archived.jpg", "archived.jpg")
    s = run(et, [good, bad, arch], tmp_path / "out")
    assert s.exported == ["0001_no_date.jpg"]
    assert [n for n, _ in s.skipped] == ["corrupt.jpg", "archived.jpg"]
    assert sorted(f.name for f in (tmp_path / "out").iterdir()) == ["0001_no_date.jpg", "captions.csv"]


@pytest.mark.parametrize(
    "name", ["sixteen_bit.png", "palette.png", "alpha.png", "cmyk.jpg", "animated.png", "multipage.tif"]
)
def test_display_robustness_modes_export(
    et: ExifToolSession, fixture: Fixture, tmp_path: Path, name: str
) -> None:
    s = run(et, [fixture(name)], tmp_path / "out", burn_caption=False)
    assert not s.skipped
    Image.open(tmp_path / "out" / s.exported[0]).load()


@pytest.mark.skipif(not any(p.suffix.lower() == ".heic" for p in real_samples()), reason="no real HEIC")
def test_real_display_p3_heic_converted_to_srgb(et: ExifToolSession, tmp_path: Path) -> None:
    import shutil

    src = next(p for p in real_samples() if p.name.startswith("owner_") and p.suffix.lower() == ".heic")
    p = Path(shutil.copy2(src, tmp_path / src.name))
    s = run(et, [p], tmp_path / "out", burn_caption=False)
    dst = tmp_path / "out" / s.exported[0]
    with Image.open(dst) as im:
        desc = ImageCms.getProfileDescription(
            ImageCms.ImageCmsProfile(__import__("io").BytesIO(im.info["icc_profile"]))
        )
        assert "sRGB" in desc and im.size == (3024, 4032) and im.getexif().get(0x0112) == 1


def test_srgb_detection_by_primaries() -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    assert ex.is_srgb_profile(srgb)
    lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
    assert not ex.is_srgb_profile(lab)


@pytest.mark.skipif(not any("_n.jpg" in p.name for p in real_samples()), reason="no Facebook sample")
def test_facebook_c2_profile_counts_as_srgb() -> None:
    src = next(p for p in real_samples() if p.name.endswith("_n.jpg"))
    assert ex.is_srgb_or_untagged(Image.open(src))


@pytest.mark.skipif(not any(p.suffix.lower() == ".heic" for p in real_samples()), reason="no real HEIC")
def test_display_p3_is_not_srgb() -> None:
    src = next(p for p in real_samples() if p.suffix.lower() == ".heic")
    assert not ex.is_srgb_or_untagged(Image.open(src))
