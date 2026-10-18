"""Download the pinned ExifTool release, verify its SHA-256, and unpack it into vendor/exiftool/.

Usage: python tools/fetch_exiftool.py [--platform windows|unix] [--archive PATH]

Windows uses the standalone build (exiftool-<ver>_64.zip). macOS and Linux use the
Perl distribution (Image-ExifTool-<ver>.tar.gz), which runs on the system Perl.
The checksums below are copied from checksums-<ver>.txt published by ExifTool
(https://exiftool.org/checksums.txt, mirrored on SourceForge).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

EXIFTOOL_VERSION = "13.59"
ARCHIVES = {
    "windows": (
        f"exiftool-{EXIFTOOL_VERSION}_64.zip",
        "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec",
    ),
    "unix": (
        f"Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz",
        "668ea3acececb7235fbd0f4900e72d5f12c9b07e5c778fd36cb1e9b5828fd65a",
    ),
}
MIRRORS = [
    "https://exiftool.org/{name}",
    "https://sourceforge.net/projects/exiftool/files/{name}/download",
]
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "exiftool"


def download(name: str) -> bytes:
    errors = []
    for mirror in MIRRORS:
        url = mirror.format(name=name)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URLs)
                return resp.read()
        except OSError as exc:
            errors.append(f"{url}: {exc}")
    raise SystemExit("Download failed:\n  " + "\n  ".join(errors))


def unpack_windows(data: bytes, dest: Path) -> Path:
    prefix = f"exiftool-{EXIFTOOL_VERSION}_64/"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            rel = info.filename[len(prefix) :]
            if rel == "exiftool(-k).exe":
                rel = "exiftool.exe"  # without "(-k)" ExifTool does not wait for a key press
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
    return dest / "exiftool.exe"


def unpack_unix(data: bytes, dest: Path) -> Path:
    prefix = f"Image-ExifTool-{EXIFTOOL_VERSION}/"
    keep = ("exiftool", "lib/", "LICENSE", "README")  # skip docs, tests, html
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            rel = member.name[len(prefix) :]
            if not rel.startswith(keep):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            assert src is not None
            target.write_bytes(src.read())
    script = dest / "exiftool"
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--platform", choices=["windows", "unix"], default="windows" if sys.platform == "win32" else "unix"
    )
    parser.add_argument("--archive", type=Path, help="use an already downloaded archive")
    args = parser.parse_args()

    name, expected = ARCHIVES[args.platform]
    data = args.archive.read_bytes() if args.archive else download(name)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"Checksum mismatch for {name}:\n  expected {expected}\n  got      {actual}")

    dest = VENDOR / args.platform
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    exe = unpack_windows(data, dest) if args.platform == "windows" else unpack_unix(data, dest)
    (dest / "VERSION").write_text(EXIFTOOL_VERSION + "\n", encoding="utf-8")
    print(f"ExifTool {EXIFTOOL_VERSION} verified (sha256 {actual[:16]}...) and installed: {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
