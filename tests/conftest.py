from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import make_fixtures  # noqa: E402

from photo_review.metadata import ExifToolSession  # noqa: E402

# Optional real photos (not committed): tests/fixtures/real or $PHOTO_REVIEW_REAL_SAMPLES
REAL_DIR = Path(os.environ.get("PHOTO_REVIEW_REAL_SAMPLES", ROOT / "tests" / "fixtures" / "real"))


@pytest.fixture(scope="session")
def et() -> Iterator[ExifToolSession]:
    with ExifToolSession() as session:
        yield session


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixtures") / "photos"
    make_fixtures.make(out, (320, 240), None)
    return out


@pytest.fixture
def fixture(fixture_dir: Path, tmp_path: Path) -> Callable[[str], Path]:
    """Copy a generated fixture into this test's temp folder and return the copy."""

    def copy(name: str, dest_name: str | None = None) -> Path:
        dst = tmp_path / (dest_name or Path(name).name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixture_dir / name, dst)
        os.chmod(dst, 0o644)
        return dst

    return copy


def real_samples() -> list[Path]:
    if not REAL_DIR.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif"}
    return sorted(p for p in REAL_DIR.iterdir() if p.suffix.lower() in exts)
