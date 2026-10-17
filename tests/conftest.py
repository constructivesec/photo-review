import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_image(path: Path, size=(120, 80), color=(200, 30, 30), exif=None, fmt=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    kwargs = {}
    if exif is not None:
        kwargs["exif"] = exif
    im.save(path, fmt, **kwargs)
    return path


@pytest.fixture
def photo_dir(tmp_path):
    src = tmp_path / "photos"
    src.mkdir()
    return src


@pytest.fixture
def keep_dir(tmp_path):
    return tmp_path / "keep"


def set_raw_fields(path: Path, desc=None, comment=None, iptc=None, xmp=None, iptc_encoding="utf-8"):
    """Write caption fields independently (to create disagreeing or legacy-encoded files)."""
    import pyexiv2

    with open(path, "rb") as f:
        img = pyexiv2.ImageData(f.read())
    try:
        exif = {}
        if desc is not None:
            exif["Exif.Image.ImageDescription"] = desc
        if comment is not None:
            exif["Exif.Photo.UserComment"] = "charset=Ascii " + comment
        if exif:
            img.modify_exif(exif)
        if iptc is not None:
            img.modify_iptc({"Iptc.Application2.Caption": iptc}, encoding=iptc_encoding)
        if xmp is not None:
            img.modify_xmp({"Xmp.dc.description": {'lang="x-default"': xmp}})
        data = img.get_bytes()
    finally:
        img.close()
    path.write_bytes(data)
