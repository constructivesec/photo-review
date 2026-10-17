# PyInstaller spec for Photo Review.
# Build on each target OS:  pyinstaller --noconfirm --clean photo_review.spec
#   Windows -> dist/PhotoReview.exe   (single file)
#   macOS   -> dist/PhotoReview.app   (app bundle)
import sys

from PyInstaller.utils.hooks import collect_all

datas = [("photo_review/resources", "photo_review/resources")]
binaries = []
hiddenimports = []

# pillow-heif ships native libheif/libde265 libraries that must travel with the app.
for pkg in ("pillow_heif",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_photo_review.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PhotoReview",
              console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="PhotoReview", upx=False)
    app = BUNDLE(
        coll,
        name="PhotoReview.app",
        bundle_identifier="com.example.photoreview",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PhotoReview",
              console=False, upx=False, runtime_tmpdir=None)
