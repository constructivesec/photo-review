# Photo Review

A desktop app (Windows and macOS) for curating a folder of photos, many of them scanned prints.
For each photo you choose **KEEP** or **ARCHIVE**, and you can add a caption, tags and an
approximate date. Everything is stored inside the photo files themselves, so other programs
(Apple Photos, Windows Explorer, Lightroom) can see it. An export prepares the kept photos for
photo book sites such as Mixbook and Shutterfly.

> **Back up your photo folder before you start.** The app writes information into your photo
> files and moves archived photos into an `_archive` folder.

## Install and run

Standalone builds: see [BUILD.md](BUILD.md). From source (Python 3.12 or newer; Python 3.11 is not supported because of a crash in PySide6 on 3.11):

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS: source .venv/bin/activate
pip install -r requirements.txt
python tools/fetch_exiftool.py          # downloads the pinned ExifTool and checks its checksum
python -m photo_review
```

On macOS the bundled ExifTool runs on the Perl that ships with macOS (`/usr/bin/perl`).

## Using the app

1. **Select Folder…** and choose the folder with your photos. Only photos directly in that folder
   are shown (JPG, JPEG, PNG, HEIC, HEIF, TIF, TIFF); subfolders are not included.
2. For each photo:

| Button | Key | What it does |
|---|---|---|
| KEEP (green, ✓) | `Y` | Marks the photo as kept (1 star); the green KEEP badge appears in the top-left corner and the photo stays on screen. Photos you rated 2 to 5 stars elsewhere keep their stars. In "Show archived", KEEP moves the photo back to the main folder. |
| ARCHIVE (red, box) | `N` | Marks the photo as archived and moves it into the `_archive` folder (same name; a number is added if that name is taken). |
| Back / Next | `←` / `→` | Previous / next photo. |
| Undo | `Ctrl+Z` (Windows), `Cmd+Z` (Mac) | Reverses the last change. You can undo many steps, back to when you opened the folder or switched views. |
| Info | `I` | Shows device, GPS, shutter speed, aperture, ISO and focal length. |
| Refresh | `F5` | Rescans the folder. |

- **Date:** type the month (1 to 12), the day and the four-digit year, then press **Save Date**
  or Enter. The day is optional: leave it blank to keep the stored day (or day 1 for a photo
  without a date). Tick **Approximate date** if you are not sure (it is ticked automatically for
  photos without a date). The time of day already stored is kept (12:00:00 for a photo without a
  date). "Stored date" shows the full value saved in the file; a warning appears if the photo's
  two date fields disagree.
- **Caption:** type it and press **Save Caption**. Grey text in the box is an older caption found
  in another field; saving replaces it. Text that cameras and scanners fill in automatically (such
  as "NOVATEK CAMERA") is not shown.
- **Tags:** type a tag and press Enter or **Add Tag**. Use the `person:`, `event:` and `place:`
  buttons to start a tag, for example `person:emma`, `event:christmas-2025`, `place:lake-house`.
  Tags are lowercase; spaces become hyphens. Click a blue tag to remove it. Grey tags with a
  lock are managed by the app (`status:archive`, `date:approximate`) or came from another
  program, and are left unchanged.
- **Show archived** switches to the photos in `_archive`. KEEP there moves a photo back to the
  main folder. **Show undated only** lists photos without a date; after you save a date, the
  photo leaves that list.
- **Sort:** Filename, Date taken (oldest first; undated photos last), File modified, or Rating.
  The order is set when you load, sort, toggle a view or filter, or press Refresh; editing a
  photo never reorders the list while you work.
- **Nothing you type is lost.** Moving to another field saves the field you left (moving between
  Month, Day and Year saves the date only when you leave the date). **Next**, **Back** and
  **ARCHIVE** first save anything typed but not yet saved (caption, tag, date). If something cannot
  be saved (for example month 13), the app stays on the photo and shows the error so you can fix it.
- While a text box is active, the single-key shortcuts type into the box. Press `Esc` to leave it
  (this also saves it).
- **File > Settings…:** "Mirror captions to legacy fields" (on) and "Also set CreateDate" (off;
  on scans, CreateDate records the scan date).

### Export for photo book (File menu)

Copies the photos in the current view, in the current order, to a new **empty** folder.
Originals are not changed and archived photos are never exported.
- Files are named `0001_<name>`, `0002_<name>`, … so the upload order matches your sort.
- HEIC and TIFF become JPEG (quality 95). JPEG stays JPEG and PNG stays PNG; photos that need no
  change are copied exactly. Colours are converted to sRGB and rotation is applied.
- **Burn caption into image** (optional) adds a black bar with the caption in white to the
  exported copies (bar height 5% of the photo, adjustable from 2% to 10%).
- `captions.csv` lists file name, date, caption and tags (opens in Excel).

## Where information is stored

| Information | Fields |
|---|---|
| Keep / archive | XMP Rating (1 = keep, -1 = archive) and the keyword `status:archive` |
| Caption | XMP Description; also EXIF ImageDescription and, for JPEG, IPTC Caption-Abstract and the Windows comment |
| Tags | XMP Subject; also IPTC Keywords for JPEG |
| Date taken | EXIF and XMP DateTimeOriginal, and XMP-photoshop DateCreated |
| Approximate date | the keyword `date:approximate` |

Image pixels are never changed (except in exported copies). Writing keeps the file's
modification date; on some systems the file's **creation** date may change when it is updated.

## Known limitations

- Live Photo `.mov` companions, `.AAE` edit files and edited-version files are **not** moved
  with their photo when it is archived.
- **Apple Photos** reads this information only when photos are imported. Curate first, then import.
- **Windows Explorer** shows a rating of -1 (archived) as no stars, may not show "Date taken"
  for PNG files, and needs Microsoft's free *HEIF Image Extensions* to show HEIC photos.
- **Mixbook and Shutterfly** may ignore captions stored in the files. Use `captions.csv` or the
  "Burn caption into image" export option.
- Captions burned into exported images use the bundled DejaVu Sans font, which has no emoji.
- One copy of the app can run at a time.

## Troubleshooting

- **File > Open log folder** opens the log (file paths and errors only; captions are never
  logged). The log is in the app's settings folder.
- If the settings file is damaged it is renamed to `config.bad` and defaults are used.
- **"maker notes ... repaired while saving"**: another program left the camera's own data in the
  photo (maker notes) with broken offsets. The app puts it back the way the camera wrote it while
  saving your change; nothing else in the file changes. If ExifTool cannot tell how to repair it,
  the save fails with ExifTool's message and the file is left untouched.

## Manual QA checklist (run on Windows and on macOS)

Use `python tools/make_fixtures.py <empty folder>` to create a test folder, plus a few real photos.

- [ ] First launch shows the backup notice; "Don't show this again" sticks after a restart.
- [ ] Starting a second copy shows "already running".
- [ ] Selecting an `_archive` folder is refused with a message.
- [ ] KEEP: green KEEP badge appears top-left, the same photo stays shown, counter unchanged. A 3-star Lightroom photo stays 3 stars.
- [ ] Type a caption, click into Month: the caption is saved and still shown. Type a date, a caption and a tag, press Next: all three are saved.
- [ ] Type month 13 and press Next or Back: the app stays on the photo and shows the error.
- [ ] ARCHIVE: photo moves to `_archive`, total drops by one; Undo brings it back to the same position.
- [ ] Show archived: KEEP moves the photo back to the main folder; Undo returns it.
- [ ] Caption with accents, emoji and a line break saves and reloads exactly.
- [ ] Tags: `Person:Emma Rose` becomes `person:emma-rose`; `status:x` and `a,b` are refused.
- [ ] Date 6 / (blank) / 1962 saves; "Stored date" shows 1962-06-01 12:00:00 for an undated photo.
- [ ] Date 6 / 15 / 1962 saves 1962-06-15; the Day field shows 15 when you come back to the photo.
- [ ] Invalid date input (13 / 1962, 6 / 62, next month, 2 / 30 / 1962, tomorrow) shows a message and writes nothing.
- [ ] Show undated only: saving a date removes the photo from the list; Undo restores it.
- [ ] Sort by date: undated photos are last; editing a date does not move the photo.
- [ ] Info panel: device shows "Scan?" for the Epson sample; GPS hidden until "Show GPS"; "Open in Maps" works.
- [ ] Corrupt photo shows a placeholder and an error; navigation continues.
- [ ] Read-only photo: KEEP/ARCHIVE show an error; the file is unchanged.
- [ ] Rotated iPhone HEIC displays upright with correct colours.
- [ ] Export: names `0001_…`, HEIC/TIFF become JPEG, `captions.csv` opens in Excel with accents intact,
      burned captions are centred; a non-empty folder is refused.
- [ ] Refresh after deleting a file outside the app skips it with a message.
- [ ] Undo with `Ctrl+Z` / `Cmd+Z`; holding a key down does not repeat the action.
- [ ] **Windows Explorer** (Properties > Details): caption, tags, rating (1 star for KEEP), date taken.
- [ ] **Apple Photos** (after import): caption, keywords, date (including 1962), favourites/rating.
- [ ] **Mixbook and Shutterfly**: upload JPEG, HEIC, PNG, TIFF, a captioned photo, a 1962 photo and an undated
      photo; record accepted formats, sort order, 1962 display and any captions or keywords shown.
      Record results in COMPATIBILITY.md.

## Development

```bash
pip install -r requirements-dev.txt
python tools/fetch_exiftool.py
ruff check . && ruff format --check .
python -m pytest                      # integration tests use the real bundled ExifTool
python spike/spike.py --samples DIR   # metadata spike against your own photos
```

The owner's real test pictures are in `tests/fixtures/owner/`. Extra local photos can go in
`tests/fixtures/real/` (not committed).
