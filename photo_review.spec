# PyInstaller spec for Photo Review. Build on each target OS (no cross-compiling):
#   python tools/fetch_exiftool.py            # pinned ExifTool for this OS into vendor/exiftool/
#   pyinstaller --noconfirm --clean photo_review.spec
# Windows -> dist/PhotoReview.exe (single file)   macOS -> dist/PhotoReview.app
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
platform_dir = "windows" if sys.platform == "win32" else "unix"
exiftool_src = root / "vendor" / "exiftool" / platform_dir
if not exiftool_src.is_dir():
    raise SystemExit(f"ExifTool missing: run  python tools/fetch_exiftool.py  first ({exiftool_src})")

datas = [
    (str(root / "photo_review" / "resources"), "photo_review/resources"),
    (str(exiftool_src), "exiftool"),  # exiftool.exe + exiftool_files (Windows) or Perl exiftool + lib (macOS)
]
binaries = []
hiddenimports = []
for pkg in ("pillow_heif", "exiftool"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(root / "run_photo_review.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtPdf", "PySide6.Qt3DCore", "PySide6.QtMultimedia"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PhotoReview", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="PhotoReview", upx=False)
    app = BUNDLE(
        coll,
        name="PhotoReview.app",
        bundle_identifier="local.photoreview",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PhotoReview", console=False, upx=False)
