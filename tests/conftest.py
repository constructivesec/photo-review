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
