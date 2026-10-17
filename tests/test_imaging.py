import os
import time
from pathlib import Path

import pytest
from PIL import Image

from photo_review import imaging
from photo_review.fileops import FileOpError
from photo_review.imaging import BAR_HEIGHT, TEXT_PADDING

from conftest import make_image


def white_columns(im: Image.Image, top: int):
    """x-range of near-white pixels in rows >= top."""
    px = im.convert("RGB").load()
    xs = [x for x in range(im.width) for y in range(top, im.height)
          if min(px[x, y]) > 200]
    return (min(xs), max(xs)) if xs else None


def test_bar_is_exactly_50px_and_black_with_white_text(photo_dir):
    src = make_image(photo_dir / "a.jpg", size=(400, 300), color=(0, 0, 255))
    out = imaging.create_captioned_copy(src, "Hello")
    assert out.name == "a_captioned.jpg"
    with Image.open(out) as im:
        assert im.size == (400, 300 + BAR_HEIGHT)
        assert im.format == "JPEG"
        rgb = im.convert("RGB")
        assert max(rgb.getpixel((2, 300 + 2))) < 30          # bar corner is black
        span = white_columns(rgb, 300)
        assert span is not None
        centre = (span[0] + span[1]) / 2
        assert abs(centre - 200) < 6                         # horizontally centred
    assert src.stat().st_size > 0  # original untouched
    with Image.open(src) as im:
        assert im.size == (400, 300)


def test_text_vertically_centered_in_bar():
    im = Image.new("RGB", (400, 100), (0, 0, 255))
    out = imaging.render_caption(im, "Hxg")
    px = out.load()
    ys = [y for y in range(100, out.height) for x in range(out.width) if min(px[x, y]) > 200]
    mid = (min(ys) + max(ys)) / 2
    assert abs(mid - (100 + BAR_HEIGHT / 2)) < 5


def test_long_text_shrinks_to_fit():
    im = Image.new("RGB", (300, 100), (0, 0, 0))
    text = "W" * 50
    font, shown = imaging.fit_font(text, 300 - 2 * TEXT_PADDING)
    assert font.getlength(shown) <= 300 - 2 * TEXT_PADDING
    out = imaging.render_caption(im, text)
    span = white_columns(out, 100)
    assert span[0] >= TEXT_PADDING - 2 and span[1] <= 300 - TEXT_PADDING + 2


def test_short_text_uses_max_font():
    font, shown = imaging.fit_font("Hi", 1000)
    assert font.size == imaging.FONT_MAX_SIZE and shown == "Hi"


def test_medium_text_wraps_to_two_lines():
    text = "Grandma and Grandpa at the lake house, summer 2019, with the whole family"
    font, lines = imaging.layout_caption(text, 500)
    assert len(lines) == 2 and " ".join(lines) == text
    assert font.size <= imaging.TWO_LINE_MAX_SIZE
    assert all(font.getlength(ln) <= 500 for ln in lines)


def test_two_lines_stay_inside_bar():
    im = Image.new("RGB", (400, 100), (0, 0, 255))
    out = imaging.render_caption(im, "word " * 30)
    px = out.load()
    ys = [y for y in range(100, out.height) for x in range(out.width) if min(px[x, y]) > 200]
    assert min(ys) > 100 and max(ys) < out.height - 1
    span = white_columns(out, 100)
    assert span[0] >= TEXT_PADDING - 2 and span[1] <= 400 - TEXT_PADDING + 2


def test_very_long_text_truncated_on_second_line():
    font, lines = imaging.layout_caption("long words " * 150, 400)
    assert font.size == imaging.FONT_MIN_SIZE and len(lines) == 2
    assert lines[1].endswith("…")
    assert all(font.getlength(ln) <= 400 for ln in lines)


def test_captioned_copy_has_caption_metadata(photo_dir):
    from photo_review import metadata
    src = make_image(photo_dir / "a.jpg")
    out = imaging.create_captioned_copy(src, "Café 😀")
    assert set(metadata.read_caption_fields(out).values().values()) == {"Café 😀"}
    assert set(metadata.read_caption_fields(src).values().values()) == {""}  # original untouched


def test_tiny_image_truncates_at_min_font():
    font, shown = imaging.fit_font("A very long caption for a tiny image", 60)
    assert font.size == imaging.FONT_MIN_SIZE
    assert shown.endswith("…") and font.getlength(shown) <= 60


def test_heic_saved_as_jpeg(photo_dir):
    src = photo_dir / "phone.HEIC"
    Image.new("RGB", (160, 120), (10, 200, 10)).save(src, "HEIF")
    out = imaging.create_captioned_copy(src, "From phone")
    assert out == photo_dir / "phone_captioned.jpg"
    with Image.open(out) as im:
        assert im.format == "JPEG" and im.size == (160, 120 + BAR_HEIGHT)


def test_png_stays_png_with_alpha(photo_dir):
    src = photo_dir / "a.png"
    Image.new("RGBA", (100, 60), (255, 0, 0, 128)).save(src)
    out = imaging.create_captioned_copy(src, "png")
    with Image.open(out) as im:
        assert im.format == "PNG" and im.size == (100, 110)


def test_orientation_applied_and_stripped_other_exif_kept(photo_dir):
    exif = Image.Exif()
    exif[0x0112] = 6        # rotate 90 CW on display
    exif[0x010F] = "TestCam"  # Make
    src = make_image(photo_dir / "rot.jpg", size=(200, 100), exif=exif.tobytes())
    out = imaging.create_captioned_copy(src, "rotated")
    with Image.open(out) as im:
        assert im.size == (100, 200 + BAR_HEIGHT)
        e = im.getexif()
        assert 0x0112 not in e
        assert e.get(0x010F) == "TestCam"


def test_replace_regenerates_from_original(photo_dir):
    src = make_image(photo_dir / "a.jpg", size=(200, 100))
    imaging.create_captioned_copy(src, "first")
    out = imaging.create_captioned_copy(src, "second")
    with Image.open(out) as im:
        assert im.size == (200, 150)  # bars never stack


def test_caption_validation(photo_dir):
    src = make_image(photo_dir / "a.jpg")
    with pytest.raises(FileOpError):
        imaging.create_captioned_copy(src, "   ")
    with pytest.raises(FileOpError):
        imaging.create_captioned_copy(src, "é" * 1001)  # 2002 UTF-8 bytes
    imaging.create_captioned_copy(src, "x" * 2000)


def test_no_file_caption_name(photo_dir):
    src = make_image(photo_dir / "IMG_NO.jpg")
    assert imaging.create_captioned_copy(src, "x").name == "IMG_captioned.jpg"


def test_corrupt_image_raises_fileop_error(photo_dir):
    bad = photo_dir / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(FileOpError):
        imaging.create_captioned_copy(bad, "x")
    with pytest.raises(Exception):
        imaging.load_for_display(bad, 100)


def test_date_taken_exif_and_fallback(photo_dir):
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2001:02:03 04:05:06"
    with_exif = make_image(photo_dir / "e.jpg", exif=exif.tobytes())
    plain = make_image(photo_dir / "p.png")
    os.utime(plain, (1_000_000_000, 1_000_000_000))
    assert time.localtime(imaging.date_taken(with_exif))[:6] == (2001, 2, 3, 4, 5, 6)
    assert imaging.date_taken(plain) == 1_000_000_000


def test_load_for_display_applies_orientation(photo_dir):
    exif = Image.Exif()
    exif[0x0112] = 6
    src = make_image(photo_dir / "rot.jpg", size=(200, 100), exif=exif.tobytes())
    im = imaging.load_for_display(src, 1000)
    assert im.size == (100, 200)
