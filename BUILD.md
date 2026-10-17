# Building standalone apps

PyInstaller cannot cross-compile: build the Windows `.exe` on Windows and the
macOS `.app` on macOS. Both use the same spec file, `photo_review.spec`, which:

- bundles `photo_review/resources` (the DejaVu Sans caption font and its license),
- collects `pillow_heif` including its native libheif/libde265 libraries (HEIC support),
- builds a single-file `PhotoReview.exe` on Windows and a `PhotoReview.app` bundle on macOS.

## Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
pyinstaller --noconfirm --clean photo_review.spec
```

Output: `dist\PhotoReview.exe`. Copy it anywhere and double-click to run.
Optionally add an icon by passing `icon="photo_review.ico"` to `EXE(...)` in the spec.

## macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
pyinstaller --noconfirm --clean photo_review.spec
```

Output: `dist/PhotoReview.app`. Drag it to Applications.

Notes:
- Build on the oldest macOS version you want to support, and on the CPU type you
  target (Apple Silicon builds run on Apple Silicon; build on Intel or use a
  universal2 Python for Intel Macs).
- Unsigned apps are blocked by Gatekeeper on first launch. For internal use,
  right-click the app and choose **Open**. For distribution, sign and notarize:

  ```bash
  codesign --deep --force --options runtime --sign "Developer ID Application: <Team>" dist/PhotoReview.app
  ditto -c -k --keepParent dist/PhotoReview.app PhotoReview.zip
  xcrun notarytool submit PhotoReview.zip --keychain-profile <profile> --wait
  xcrun stapler staple dist/PhotoReview.app
  ```

## Equivalent command line (without the spec file)

PyInstaller 6+ accepts `:` as the `--add-data` separator on all platforms.

```bash
pyinstaller --noconfirm --clean --windowed --name PhotoReview \
  --add-data "photo_review/resources:photo_review/resources" \
  --collect-all pillow_heif \
  run_photo_review.py
```

Add `--onefile` on Windows for a single `.exe`.

## Checking a build

1. Launch the app and open a folder containing a `.heic` photo: it should display.
2. Submit a caption: `<name>_captioned.jpg` should appear with the white text in a black bar.
3. If HEIC photos show the "Cannot display" placeholder, pillow-heif's native
   libraries were not bundled; rebuild with `--clean` and check the PyInstaller
   log for `pillow_heif`.
