import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from photo_review import metadata
from photo_review.fileops import FileOpError
from photo_review.metadata import CaptionFields, choose_caption, decode_user_comment

from conftest import make_image, set_raw_fields

HAVE_EXIFTOOL = shutil.which("exiftool") is not None


def fields_of(path):
    return metadata.read_caption_fields(path).values()


def rich_jpeg(path: Path) -> Path:
    """JPEG with camera EXIF, GPS and an ICC profile."""
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    exif[0x0110] = "Model X"
    exif.get_ifd(0x8769)[0x9003] = "2020:01:02 03:04:05"
    exif.get_ifd(0x8825)[2] = (40.0, 26.0, 46.0)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (300, 200), (120, 30, 200)).save(path, "JPEG", exif=exif.tobytes(), icc_profile=icc)
    return path


# ---------------------------------------------------------------- reading / choosing

def test_blank_photo_has_no_caption(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    assert choose_caption(metadata.read_caption_fields(p)).text == ""


@pytest.mark.parametrize("field", ["desc", "comment", "iptc", "xmp"])
def test_each_field_alone_is_read(photo_dir, field):
    p = make_image(photo_dir / "a.jpg")
    set_raw_fields(p, **{field: "Only here"})
    choice = choose_caption(metadata.read_caption_fields(p))
    assert choice.text == "Only here" and not choice.disagree


def test_disagreement_prefers_iptc(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    set_raw_fields(p, desc="d", comment="c", iptc="i", xmp="x")
    choice = choose_caption(metadata.read_caption_fields(p))
    assert (choice.text, choice.source, choice.disagree) == ("i", "iptc", True)


def test_disagreement_without_iptc_uses_fallback_order():
    f = CaptionFields(image_description="d", user_comment="c", xmp="x")
    assert choose_caption(f).text == "x"
    f = CaptionFields(image_description="d", user_comment="c")
    assert choose_caption(f).text == "c"


def test_equal_fields_after_normalization_agree():
    f = CaptionFields(image_description="Café ", iptc="Café", xmp="  Café\x00")
    f = CaptionFields(**{k: metadata.normalize(v) for k, v in f.values().items()})
    choice = choose_caption(f)
    assert choice.text == "Café" and not choice.disagree


def test_camera_placeholders_are_blank(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    set_raw_fields(p, desc="OLYMPUS DIGITAL CAMERA", comment="   ", xmp="Real caption")
    choice = choose_caption(metadata.read_caption_fields(p))
    assert choice.text == "Real caption" and not choice.disagree


def test_legacy_latin1_iptc_without_charset(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    set_raw_fields(p, iptc="Café", iptc_encoding="latin-1")
    assert fields_of(p)["iptc"] == "Café"


@pytest.mark.parametrize("raw,big,expected", [
    (b"ASCII\0\0\0Hello", False, "Hello"),
    (b"UNICODE\0" + "Café 😀".encode("utf-16-le"), False, "Café 😀"),
    (b"UNICODE\0" + "Café 😀".encode("utf-16-be"), True, "Café 😀"),
    (b"UNICODE\0" + "Café".encode("utf-16-be"), False, "Café"),  # wrong byte order recovered
    (b"\0" * 8 + "Café".encode("utf-8"), False, "Café"),
    (b"JIS\0\0\0\0\0" + "日本".encode("iso2022_jp"), False, "日本"),
])
def test_decode_user_comment(raw, big, expected):
    assert decode_user_comment(raw, big) == expected


def test_heic_fields_are_read(photo_dir):
    exif = Image.Exif()
    exif[0x010E] = "From phone"
    p = photo_dir / "a.heic"
    Image.new("RGB", (64, 48)).save(p, "HEIF", exif=exif.tobytes())
    assert choose_caption(metadata.read_caption_fields(p)).text == "From phone"


# ---------------------------------------------------------------- writing

@pytest.mark.parametrize("name", ["a.jpg", "b.png"])
@pytest.mark.parametrize("text", ["Hello", "Café 日本 😀", "x" * 300])
def test_write_sets_all_four_fields(photo_dir, name, text):
    p = make_image(photo_dir / name)
    metadata.write_caption(p, text)
    assert set(fields_of(p).values()) == {text}


def test_write_preserves_pixels_and_other_metadata(photo_dir):
    p = rich_jpeg(photo_dir / "a.jpg")
    before = p.read_bytes()
    metadata.write_caption(p, "New caption")
    after = p.read_bytes()
    assert metadata.pixel_payload(before) == metadata.pixel_payload(after)
    with Image.open(p) as im:
        exif = im.getexif()
        assert exif[0x010F] == "TestCam" and exif[0x0110] == "Model X"
        assert exif.get_ifd(0x8769)[0x9003] == "2020:01:02 03:04:05"
        assert exif.get_ifd(0x8825)[2] == (40.0, 26.0, 46.0)
        assert im.info["icc_profile"] == Image.open(io.BytesIO(before)).info["icc_profile"]


def test_png_pixels_preserved(photo_dir):
    p = photo_dir / "a.png"
    Image.new("RGBA", (50, 40), (1, 2, 3, 128)).save(p)
    before = p.read_bytes()
    metadata.write_caption(p, "Ünïcödé 😀")
    assert metadata.pixel_payload(before) == metadata.pixel_payload(p.read_bytes())
    Image.open(p).verify()  # chunk CRCs valid


def test_write_keeps_other_xmp_languages(photo_dir):
    import pyexiv2
    p = make_image(photo_dir / "a.jpg")
    img = pyexiv2.ImageData(p.read_bytes())
    img.modify_xmp({"Xmp.dc.description": {'lang="x-default"': "old", 'lang="fr"': "ancien"},
                    "Xmp.dc.subject": ["family"]})
    p.write_bytes(img.get_bytes())
    img.close()
    metadata.write_caption(p, "new")
    img = pyexiv2.ImageData(p.read_bytes())
    xmp = img.read_xmp()
    img.close()
    assert xmp["Xmp.dc.description"] == {'lang="x-default"': "new", 'lang="fr"': "ancien"}
    assert xmp["Xmp.dc.subject"] == ["family"]


def test_clear_removes_all_fields(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    metadata.write_caption(p, "temp")
    metadata.write_caption(p, "")
    assert set(fields_of(p).values()) == {""}


def test_modified_time_and_mode_preserved(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    os.utime(p, (1_000_000_000, 1_100_000_000))
    os.chmod(p, 0o640)
    metadata.write_caption(p, "keep my dates")
    assert p.stat().st_mtime == 1_100_000_000
    assert p.stat().st_mode & 0o777 == 0o640


def test_read_only_file_refused(photo_dir, monkeypatch):
    p = make_image(photo_dir / "a.jpg")
    before = p.read_bytes()
    monkeypatch.setattr(metadata.os, "access", lambda path, mode: False)
    with pytest.raises(FileOpError, match="read-only"):
        metadata.write_caption(p, "x")
    assert p.read_bytes() == before
    assert [f.name for f in photo_dir.iterdir()] == ["a.jpg"]  # no temp files left


def test_damaged_exif_refused(photo_dir):
    good = io.BytesIO()
    Image.new("RGB", (16, 16)).save(good, "JPEG")
    data = good.getvalue()
    seg = b"Exif\0\0" + b"II*\0\x08\0\0\0" + b"\xff\x7f" + b"\0" * 40
    p = photo_dir / "bad.jpg"
    p.write_bytes(data[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + data[2:])
    before = p.read_bytes()
    with pytest.raises(FileOpError, match="damaged"):
        metadata.write_caption(p, "x")
    assert p.read_bytes() == before


def test_heic_not_writable(photo_dir):
    p = photo_dir / "a.heic"
    Image.new("RGB", (16, 16)).save(p, "HEIF")
    with pytest.raises(FileOpError):
        metadata.write_caption(p, "x")


def test_too_long_refused(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    with pytest.raises(FileOpError):
        metadata.write_caption(p, "é" * 1001)


# ---------------------------------------------------------------- interoperability (ExifTool)

def exiftool(*args):
    return subprocess.run(["exiftool", *args], capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
@pytest.mark.parametrize("name", ["a.jpg", "b.png"])
def test_exiftool_reads_all_fields(photo_dir, name):
    p = make_image(photo_dir / name)
    metadata.write_caption(p, "Café 日本 😀")
    out = exiftool("-s3", "-ImageDescription", "-UserComment", "-Caption-Abstract",
                   "-XMP-dc:Description", "-MWG:Description", str(p))
    assert out.splitlines() == ["Café 日本 😀"] * 5


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_big_endian_exif_user_comment(photo_dir):
    src = make_image(photo_dir / "src.jpg")
    p = photo_dir / "be.jpg"
    exiftool("-o", str(p), "-ExifByteOrder=Big-endian", "-Make=Cam", str(src))
    metadata.write_caption(p, "Big 😀")
    assert exiftool("-s3", "-ExifByteOrder", str(p)).strip().startswith("Big-endian")
    assert exiftool("-s3", "-UserComment", str(p)).strip() == "Big 😀"
    assert fields_of(p)["user_comment"] == "Big 😀"


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_iptc_digest_updated(photo_dir):
    p = make_image(photo_dir / "a.jpg")
    exiftool("-overwrite_original", "-IPTC:Caption-Abstract=old", "-IPTCDigest=new", str(p))
    metadata.write_caption(p, "fresh")
    stored, current = exiftool("-s3", "-IPTCDigest", "-CurrentIPTCDigest", str(p)).split()
    assert stored == current


@pytest.mark.skipif(not HAVE_EXIFTOOL, reason="exiftool not installed")
def test_camera_file_with_interop_ifd(photo_dir):
    # Camera JPEGs carry an Interoperability IFD pointer (a file offset that moves on rewrite).
    p = rich_jpeg(photo_dir / "cam.jpg")
    exiftool("-overwrite_original", "-InteropIndex=R98", "-ImageDescription=OLYMPUS DIGITAL CAMERA", str(p))
    metadata.write_caption(p, "Real photo")
    assert set(fields_of(p).values()) == {"Real photo"}
    assert exiftool("-s3", "-InteropIndex", str(p)).startswith("R98")
