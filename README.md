# Photo Review

A simple desktop app (Windows and macOS) for quickly culling a folder of photos.
For each photo, press **YES** to move it to a Keep folder or **NO** to mark it
with `_NO` in its filename. You can also save a captioned copy of any photo.

## Install and run from source

Requires Python 3.11 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m photo_review
```

For standalone builds (`PhotoReview.exe` / `PhotoReview.app`), see [BUILD.md](BUILD.md).

## Using the app

1. Click **Select Folder…** and choose a folder of photos. Only `.jpg`, `.jpeg`,
   `.png`, `.heic` and `.heif` files directly in that folder are shown
   (subfolders, videos and Live Photo `.mov` files are ignored).
2. Check the **Keep folder** path. By default it is a `Keep` folder inside the
   photo folder. Type a different path or click **Browse…**. The folder is
   created automatically the first time you press YES. It may not be the same
   as the photo folder.
3. Review each photo:

| Button | Key | What it does |
|---|---|---|
| YES (green) | `Y` | Moves the photo to the Keep folder and shows the next one. |
| NO (red) | `N` | Renames the photo `name_NO.jpg`, leaves it in place, and shows the next one. |
| Next / Back | `Right` / `Left` | Moves between photos. |
| Undo | `Ctrl+Z` (Windows), `Cmd+Z` (macOS) | Reverses the last action. You can undo repeatedly, back to when you opened the folder. |

- The counter (for example `12 of 128`) shows where you are. YES removes the
  photo from the list, so the total goes down.
- Photos already marked NO show a large red **NO** badge. You can still press
  YES on them; the `_NO` is removed from the name when the photo is moved.
- **Hide NO photos** skips photos marked NO.
- **Sort** orders photos by filename, date taken (from the camera's EXIF data,
  or the file date if missing), or date modified.
- The app remembers the last folder, Keep folder and sort choice. It always
  starts at the first photo.

### Captions

Type up to 50 characters in the **Caption** box and press **Submit** (or Enter).
The app saves a **copy** of the photo with a 50-pixel black bar along the bottom
containing your caption in white. The original photo is never changed.

- While the caption box is active, the `Y`/`N`/arrow keys type text instead of
  acting on photos. Press `Esc` or click the photo to leave the box.
- Submitting again for the same photo replaces the captioned copy (bars never stack).
- When you press YES on a photo, its captioned copy moves to the Keep folder too.

**Captioned copy naming rule:** `<name>_captioned.<ext>` in the same folder as
the original, where `<name>` is the original filename without any `_NO`.
For example, `IMG_001.jpg` and `IMG_001_NO.jpg` both produce
`IMG_001_captioned.jpg`. HEIC/HEIF photos produce a `.jpg` copy; JPEG and PNG
keep their format. Files ending in `_captioned` are never shown in the review
list. (If a folder contains both `IMG_1.jpg` and `IMG_1.heic`, they share the
same captioned filename and the newest caption wins.)

## Details and safety

- Originals are never deleted or overwritten. The only file changes are: move
  (YES), rename (NO), create/replace a captioned copy, and undoing those.
- If a filename is already taken, a number is added: `name_1.jpg`, `name_2.jpg`
  (for NO: `name_1_NO.jpg`).
- Pressing NO on a photo already marked NO does nothing except move to the next photo.
- Moves to another drive or network share are done as copy, verify (size and
  SHA-256), then delete the original. If anything fails, the original stays put
  and an error appears in the status bar.
- Unreadable or corrupt images show a placeholder; you can still YES/NO them.
- Undo of a caption restores the previous captioned copy (backed up in a
  temporary folder for the session) or deletes the newly created one.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Project layout:

| Module | Purpose |
|---|---|
| `photo_review/fileops.py` | Scanning, naming rules, collision-safe move/rename |
| `photo_review/imaging.py` | Decoding, EXIF dates, caption rendering |
| `photo_review/session.py` | Working list, sort/filter, YES/NO/caption, undo (no Qt) |
| `photo_review/config.py` | JSON settings in the OS config folder (`platformdirs`) |
| `photo_review/loader.py` | Background decoding and EXIF date scanning |
| `photo_review/ui.py`, `widgets.py` | Qt main window, image view, icons |

The bundled font is DejaVu Sans (`photo_review/resources/fonts`, Bitstream Vera
license, see `LICENSE-DejaVu.txt`), so captions render identically on every OS.
