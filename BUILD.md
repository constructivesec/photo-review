# Building Photo Review

PyInstaller cannot cross-compile: build the Windows `.exe` on Windows and the macOS `.app` on a
Mac. Both use `photo_review.spec`.

## What gets bundled

| Item | Source | Notes |
|---|---|---|
| ExifTool 13.59 | `tools/fetch_exiftool.py` downloads it from exiftool.org (falling back to ExifTool's SourceForge mirror) and **verifies its SHA-256** against the pinned value | Windows: the standalone `exiftool-13.59_64.zip` (`exiftool.exe` plus its `exiftool_files` folder). macOS: the Perl distribution `Image-ExifTool-13.59.tar.gz` (script plus `lib/`). Bundled into `exiftool/` inside the app. |
| ExifTool config | `photo_review/resources/exiftool_config.pl` | Stores the Windows comment (XPComment) as UTF-16 so emoji survive (decision Q1). |
| Caption font | `photo_review/resources/fonts/DejaVuSans.ttf` | DejaVu Sans, Bitstream Vera license (`LICENSE-DejaVu.txt`); redistribution allowed. |
| HEIC support | `pillow-heif` (libheif, libde265 and friends) | Collected with `collect_all("pillow_heif")`. |

Pinned checksums (from ExifTool's `checksums-13.59.txt`):

```
exiftool-13.59_64.zip        44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec
Image-ExifTool-13.59.tar.gz  668ea3acececb7235fbd0f4900e72d5f12c9b07e5c778fd36cb1e9b5828fd65a
```

Compare these once with https://exiftool.org/checksums.txt. ExifTool 13.59 is well past 12.24,
the first release fixing CVE-2021-22204. The version is shown in **Help > About**.

### macOS: system Perl (decision Q22)

The Mac build uses the **system Perl** (`/usr/bin/perl`, included with macOS) to run the bundled
ExifTool; Perl itself is not bundled. Apple has said scripting runtimes may be removed from a
future macOS. If that happens, the app shows "Perl was not found" at startup; the fix is to
install Perl (for example with Homebrew) or switch to bundling a Perl runtime.

## Windows

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python tools\fetch_exiftool.py --platform windows
python -m pytest
pyinstaller --noconfirm --clean photo_review.spec
```

Output: `dist\PhotoReview.exe`, a single file. It unpacks itself to a temporary folder at each
start, so the first window takes a few seconds to appear.

**SmartScreen:** an unsigned `.exe` downloaded from the internet triggers "Windows protected your
PC". Choose **More info > Run anyway**. Signing the exe with a code-signing certificate
(`signtool sign /fd SHA256 /a dist\PhotoReview.exe`) removes the warning over time.

## macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python tools/fetch_exiftool.py --platform unix
python -m pytest
pyinstaller --noconfirm --clean photo_review.spec
```

Output: `dist/PhotoReview.app`. Build on the oldest macOS you want to support, on the CPU type you
target (Apple Silicon or Intel).

**Gatekeeper:** an unsigned app is blocked on first launch ("cannot be opened because the
developer cannot be verified"). Right-click the app, choose **Open**, then **Open** again; or
allow it in **System Settings > Privacy & Security**. For distribution, sign and notarize:

```bash
codesign --deep --force --options runtime --sign "Developer ID Application: <name>" dist/PhotoReview.app
ditto -c -k --keepParent dist/PhotoReview.app PhotoReview.zip
xcrun notarytool submit PhotoReview.zip --keychain-profile <profile> --wait
xcrun stapler staple dist/PhotoReview.app
```

## Checking a build

1. Start the app: no "ExifTool could not be started" message; **Help > About** shows ExifTool 13.59.
2. Open a folder created with `python tools/make_fixtures.py <empty folder>`; the iPhone HEIC displays.
3. KEEP a photo, then check it with the bundled ExifTool or Explorer/Finder: Rating = 1.
4. Export with "Burn caption into image": the caption bar renders in DejaVu Sans.
5. Run the manual QA checklist in README.md.
