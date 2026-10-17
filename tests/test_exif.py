import os
import time
from fractions import Fraction

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from photo_review import exif as ex
from photo_review import imaging

from conftest import make_image

MTIME = 1_000_000_000  # 2001-09-09


def full_exif(date="2001:02:03 04:05:06", gps=True) -> Image.Exif:
    e = Image.Exif()
    e[ex.EXIF_MAKE] = "Canon"
    e[ex.EXIF_MODEL] = "Canon EOS R5"
    sub = e.get_ifd(ex.EXIF_IFD)
    if date:
        sub[0x9003] = date
    sub[ex.EXIF_EXPOSURE_TIME] = IFDRational(1, 250)
    sub[ex.EXIF_F_NUMBER] = IFDRational(28, 10)
    sub[ex.EXIF_ISO] = 400
    sub[ex.EXIF_FOCAL_LENGTH] = IFDRational(50, 1)
    if gps:
        g = e.get_ifd(ex.GPS_IFD)
        g[ex.GPS_LAT_REF] = "S"
        g[ex.GPS_LAT] = (IFDRational(33, 1), IFDRational(52, 1), IFDRational(0, 1))
        g[ex.GPS_LON_REF] = "W"
        g[ex.GPS_LON] = (IFDRational(70, 1), IFDRational(30, 1), IFDRational(36, 1))
    return e


def with_mtime(path, ts=MTIME):
    os.utime(path, (ts, ts))
    return path


def inject_app1(path, payload: bytes):
    """Insert a raw APP1 segment right after the JPEG SOI marker."""
    data = path.read_bytes()
    seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    path.write_bytes(data[:2] + seg + data[2:])
    return path


# ---------------------------------------------------------------- date fallback

def test_date_uses_datetime_original(photo_dir):
    p = with_mtime(make_image(photo_dir / "a.jpg", exif=full_exif().tobytes()))
    info = ex.read_info(p)
    assert time.localtime(info.date)[:6] == (2001, 2, 3, 4, 5, 6)
    assert not info.date_is_modified
    assert "(modified)" not in ex.format_date(info)


def test_date_falls_back_to_ifd0_datetime(photo_dir):
    e = Image.Exif()
    e[0x0132] = "2010:06:07 08:09:10"
    p = with_mtime(make_image(photo_dir / "a.jpg", exif=e.tobytes()))
    info = ex.read_info(p)
    assert time.localtime(info.date)[:6] == (2010, 6, 7, 8, 9, 10)
    assert not info.date_is_modified


def test_date_falls_back_to_modified_without_exif(photo_dir):
    p = with_mtime(make_image(photo_dir / "a.jpg"))
    info = ex.read_info(p)
    assert info.date == MTIME and info.date_is_modified
    assert ex.format_date(info).endswith("(modified)")
    assert not info.has_exif


@pytest.mark.parametrize("bad", ["garbage", "0000:00:00 00:00:00", "2001:13:45 99:99:99", ""])
def test_unparseable_exif_date_falls_back_to_modified(photo_dir, bad):
    p = with_mtime(make_image(photo_dir / "a.jpg", exif=full_exif(date=bad).tobytes()))
    info = ex.read_info(p)
    assert info.date == MTIME and info.date_is_modified
    assert info.has_exif and info.make == "Canon"  # other fields still read


def test_resolve_date_with_no_exif_object(photo_dir):
    p = with_mtime(make_image(photo_dir / "a.jpg"))
    assert ex.resolve_date(None, p) == (MTIME, True)


# ---------------------------------------------------------------- fields

def test_all_fields_read_and_formatted(photo_dir):
    p = make_image(photo_dir / "a.jpg", size=(400, 300), exif=full_exif().tobytes())
    info = ex.read_info(p)
    assert info.has_exif
    assert ex.format_camera(info) == "Canon EOS R5"
    assert ex.format_shutter(info.exposure_time) == "1/250 s"
    assert ex.format_aperture(info.f_number) == "f/2.8"
    assert ex.format_iso(info.iso) == "ISO 400"
    assert ex.format_focal(info.focal_length) == "50 mm"
    lat, lon = info.gps
    assert lat == pytest.approx(-33.866667) and lon == pytest.approx(-70.51)
    assert ex.format_gps(info.gps) == "-33.866667, -70.510000"
    assert "query=-33.866667,-70.510000" in ex.maps_url(info.gps)


def test_info_line(photo_dir):
    p = with_mtime(make_image(photo_dir / "a.jpg", size=(4000, 3000)))
    info = ex.read_info(p)
    line = ex.info_line(info, "a.jpg")
    parts = line.split(ex.SEP)
    assert parts[0].endswith("(modified)")
    assert parts[1] == "a.jpg"
    assert parts[2] == "4000 × 3000 (12.0 MP)"
    assert parts[3] == ex.format_size(p.stat().st_size)
    assert "\n" not in line


def test_dimensions_respect_orientation(photo_dir):
    e = Image.Exif()
    e[0x0112] = 6
    p = make_image(photo_dir / "rot.jpg", size=(200, 100), exif=e.tobytes())
    info = ex.read_info(p)
    assert (info.width, info.height) == (100, 200)


def test_png_exif(photo_dir):
    p = photo_dir / "a.png"
    Image.new("RGB", (64, 48)).save(p, exif=full_exif().tobytes())
    info = ex.read_info(p)
    assert info.has_exif and not info.date_is_modified and info.iso == 400


def test_heic_exif(photo_dir):
    if not imaging.HEIF_AVAILABLE:
        pytest.skip("pillow-heif not installed")
    p = photo_dir / "a.heic"
    Image.new("RGB", (64, 48)).save(p, "HEIF", exif=full_exif().tobytes())
    info = ex.read_info(p)
    assert info.has_exif and ex.format_camera(info) == "Canon EOS R5"
    assert not info.date_is_modified and info.gps is not None


def test_load_with_info_reads_full_size_before_draft(photo_dir):
    p = make_image(photo_dir / "big.jpg", size=(2000, 1000), exif=full_exif().tobytes())
    im, info = ex.load_with_info(p, 300)
    assert max(im.size) <= 300
    assert (info.width, info.height) == (2000, 1000) and info.has_exif


# ---------------------------------------------------------------- missing / corrupt

def test_missing_fields_show_na(photo_dir):
    e = Image.Exif()
    e[ex.EXIF_MODEL] = "X100V"
    p = make_image(photo_dir / "a.jpg", exif=e.tobytes())
    info = ex.read_info(p)
    assert info.has_exif and ex.format_camera(info) == "X100V"
    assert info.gps is None and ex.format_gps(info.gps) == ex.NA
    assert ex.format_shutter(info.exposure_time) == ex.NA
    assert ex.format_aperture(info.f_number) == ex.NA
    assert ex.format_iso(info.iso) == ex.NA
    assert ex.format_focal(info.focal_length) == ex.NA


def test_malformed_values_are_ignored(photo_dir):
    e = Image.Exif()
    e[ex.EXIF_MAKE] = b"\xff\xfeBad\x00\x00"
    sub = e.get_ifd(ex.EXIF_IFD)
    sub[ex.EXIF_EXPOSURE_TIME] = IFDRational(1, 0)   # division by zero
    sub[ex.EXIF_F_NUMBER] = "f2.8"                   # wrong type
    sub[ex.EXIF_FOCAL_LENGTH] = IFDRational(-5, 1)  # nonsense
    g = e.get_ifd(ex.GPS_IFD)
    g[ex.GPS_LAT] = (IFDRational(95, 1), IFDRational(0, 1), IFDRational(0, 1))  # > 90
    g[ex.GPS_LON] = (IFDRational(10, 1),)                                          # too short
    p = make_image(photo_dir / "a.jpg", exif=e.tobytes())
    info = ex.read_info(p)
    assert info.has_exif
    assert info.exposure_time is None and info.f_number is None and info.focal_length is None
    assert info.gps is None
    assert "\x00" not in info.make


def test_parse_gps_rejects_bad_input():
    assert ex.parse_gps({}) is None
    assert ex.parse_gps({ex.GPS_LAT: "north", ex.GPS_LON: None}) is None
    assert ex.parse_gps({ex.GPS_LAT: (1, 2, 3), ex.GPS_LON: (4, 5, Fraction(6))}) == pytest.approx(
        (1 + 2 / 60 + 3 / 3600, 4 + 5 / 60 + 6 / 3600))


@pytest.mark.parametrize("payload", [
    b"Exif\x00\x00" + b"\xde\xad\xbe\xef" * 8,             # not a TIFF header
    b"Exif\x00\x00II*\x00\xff\xff\xff\x7f",                # IFD offset past the end
    b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\xff\xff",        # truncated IFD entry count
])
def test_corrupt_exif_block_never_raises(photo_dir, payload):
    p = with_mtime(inject_app1(make_image(photo_dir / "a.jpg"), payload))
    info = ex.read_info(p)
    assert info.date == MTIME and info.date_is_modified
    assert ex.info_line(info, p.name)
    im, info2 = ex.load_with_info(p, 1000)  # still decodes
    assert im.size == (120, 80) and info2.date_is_modified


def test_unreadable_file_gives_basic_info(photo_dir):
    p = photo_dir / "bad.jpg"
    p.write_bytes(b"not an image")
    with_mtime(p)
    info = ex.read_info(p)
    assert not info.has_exif and info.date_is_modified and info.file_size == 12
    assert ex.format_dimensions(info) == ex.NA
    assert ex.info_line(info, p.name).split(ex.SEP)[1:] == ["bad.jpg", ex.NA, "12 B"]


def test_vanished_file_gives_basic_info(photo_dir):
    info = ex.read_info(photo_dir / "gone.jpg")
    assert info.file_size is None and info.date_is_modified
    assert ex.format_size(info.file_size) == ex.NA


# ---------------------------------------------------------------- formatting

@pytest.mark.parametrize("seconds,text", [
    (1 / 250, "1/250 s"), (1 / 3, "1/3 s"), (0.3, "0.3 s"), (2, "2 s"), (1.5, "1.5 s"),
    (1 / 8000, "1/8000 s"), (None, ex.NA),
])
def test_format_shutter(seconds, text):
    assert ex.format_shutter(seconds) == text


@pytest.mark.parametrize("size,text", [
    (0, "0 B"), (1023, "1023 B"), (2048, "2.0 KB"), (int(2.4 * 1024 ** 2), "2.4 MB"),
    (3 * 1024 ** 3, "3.0 GB"),
])
def test_format_size(size, text):
    assert ex.format_size(size) == text
