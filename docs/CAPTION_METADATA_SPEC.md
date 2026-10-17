# Caption Metadata: Specification

Status: Approved and implemented (decisions in section 10)
Extends: Photo Review App Specification, sections 6 (Captions) and 7 (Non-Functional Requirements)

## 1. Purpose

Read, display, and write a photo's caption using the four standard metadata fields that
photo software uses for descriptions. The caption stays with the photo itself, so other tools
(Lightroom, Apple Photos, Google Photos, Windows Explorer) can see it.

## 2. Fields

| ID | Field | Location | Data type and encoding |
|---|---|---|---|
| F1 | EXIF ImageDescription | Tag 0x010E, IFD0 | ASCII-typed. Many tools store UTF-8 there anyway. |
| F2 | EXIF UserComment | Tag 0x9286, Exif SubIFD | UNDEFINED. Starts with an 8-byte character-code prefix: `ASCII\0\0\0`, `UNICODE\0` (UTF-16), `JIS\0\0\0\0\0`, or 8 NUL bytes (undefined). |
| F3 | IPTC Caption-Abstract | IIM record 2, dataset 120 | Max 2000 bytes. The character set comes from record 1:90 (CodedCharacterSet). `ESC % G` means UTF-8. |
| F4 | XMP dc:description | `dc:description` in the `http://purl.org/dc/elements/1.1/` namespace | `rdf:Alt` language alternative. The `x-default` entry is the caption. UTF-8. |

Where each block lives in each file format:

| Format | EXIF (F1, F2) | IPTC (F3) | XMP (F4) |
|---|---|---|---|
| JPEG | APP1 `Exif\0\0` | APP13 `Photoshop 3.0`, 8BIM resource 0x0404 | APP1 `http://ns.adobe.com/xap/1.0/\0` (plus ExtendedXMP if present) |
| PNG | `eXIf` chunk (older files: `zTXt`/`tEXt` "Raw profile type exif") | No standard. De facto: `zTXt`/`tEXt` "Raw profile type iptc" (ExifTool, ImageMagick, Exiv2 convention) | `iTXt` keyword `XML:com.adobe.xmp` |
| HEIC/HEIF | `Exif` item | Not supported by the format | `mime` item, content type `application/rdf+xml` |

Out of scope (read only to the extent listed, never written): Windows XPTitle/XPComment/XPSubject,
IPTC Headline (2:105), XMP `dc:title`, `photoshop:Headline`, and `exif:UserComment` mirrored in XMP.
The XMP `exif:UserComment` and `tiff:ImageDescription` mirrors are not read, but if present they
are updated together with F1 and F2 so the file stays consistent (see 5.3).

## 3. Reading

### 3.1 When
- Metadata is read in the background worker that already decodes the photo, and cached with the image.
- A read failure in one field never blocks display or the other fields. That field counts as blank,
  and the status bar shows a non-blocking warning ("Caption metadata could not be read: ...").

### 3.2 Decoding rules
| Field | Rule |
|---|---|
| F1 | Decode as UTF-8. If that fails, decode as Windows-1252. |
| F2 | `ASCII` prefix: ASCII, with Windows-1252 fallback. `UNICODE` prefix: UTF-16 in the EXIF block's byte order (`II` = little-endian, `MM` = big-endian). If the result is unreadable, retry the other byte order. Undefined prefix: try UTF-8, then Windows-1252. `JIS`: decode as ISO-2022-JP. |
| F3 | If 1:90 is `ESC % G`, decode as UTF-8. Otherwise try UTF-8, then Windows-1252. If several 2:120 datasets exist, use the first. |
| F4 | Use the `x-default` alternative. If there is none, use the first `rdf:li`. A plain (non-Alt) string value is accepted as well. |

### 3.3 Normalization (applied to every value before comparing)
1. Strip trailing NUL bytes.
2. Trim leading and trailing whitespace.
3. Unicode NFC normalization.
4. Treat the value as **blank** if it is empty or matches a known camera placeholder
   (case-insensitive, after trimming): `OLYMPUS DIGITAL CAMERA`, `SONY DSC`,
   `DIGITAL CAMERA`, `KONICA MINOLTA DIGITAL CAMERA`, `DCIM\100MEDIA`, `default`, `Exif_JPEG_PICTURE`.
   Keep this list in one constant so it can be extended.

Comparison after normalization is exact and case-sensitive.

### 3.4 Choosing the caption to display
1. Collect the non-blank values of F1 to F4.
2. None: the caption box is empty.
3. All non-blank values are identical: show that value.
4. Non-blank values differ:
   - Show IPTC Caption-Abstract (F3) if it is non-blank.
   - If F3 is blank, use this order: XMP dc:description (F4), then UserComment (F2), then ImageDescription (F1).
     (Applies to HEIC, which has no IPTC, and to files where only EXIF and XMP disagree.)
   - Show an amber notice next to the box: "Caption fields disagree. Showing IPTC caption."
     (or whichever field was chosen). Its tooltip lists each field and its value.

Note: The Metadata Working Group (MWG) guidelines use the IPTC digest to decide between IPTC
and XMP. This spec follows the fixed rule requested (IPTC wins) and ignores the digest when reading.

### 3.5 Showing it in the caption box
- When a photo is displayed, the box is filled with the chosen caption, or cleared if there is none.
  This replaces the current rule "the caption input clears whenever the displayed photo changes."
- If metadata arrives after the user has started typing for this photo, the user's text is kept.
- The box accepts up to 2000 bytes of UTF-8 (the IPTC limit). The counter shows the space left.
  This replaces the 50-character limit in section 6 of the main spec.

## 4. Writing

### 4.1 Trigger
A caption is saved in two situations:
1. **Submit** (button or Enter): always saves the box's text. If the fields disagree, this also syncs them.
2. **Leaving the photo** with an edited or cleared caption: Next, Back, YES, NO, changing the sort,
   toggling Hide NO, opening another folder, or closing the app. The caption is saved first, then the
   action runs (so on YES, the updated captioned copy moves to Keep with the photo).
   "Edited" means the trimmed box text differs from the photo's stored caption. A caption that was only
   displayed is never written back.

What a save does:
- **Non-blank text**: writes all applicable fields of the original photo, and creates or replaces the captioned copy.
- **Blank text (cleared box)**: removes the caption from all four fields and deletes the captioned copy.
  Submit on an empty box for a photo that has no caption is an error ("Type a caption first.").
- If every field already holds exactly this text, the original file is not modified.
- If saving fails while leaving a photo, the move still happens and the status bar shows
  "Caption not saved: <reason>".
- **Undo button with an unsaved edit**: the first press discards the edit and restores the stored caption
  in the box. The next press undoes the last saved action as usual.

### 4.2 What is written
| Field | Value written |
|---|---|
| F1 | Caption as UTF-8, NUL-terminated (the de facto practice used by ExifTool and Windows). |
| F2 | `ASCII\0\0\0` + text if the caption is pure ASCII. Otherwise `UNICODE\0` + UTF-16 in the EXIF block's byte order, no BOM. |
| F3 | Caption as UTF-8. Set 1:90 to `ESC % G`. If the UTF-8 form is longer than 2000 bytes, refuse (the caption box prevents this). |
| F4 | Set the `x-default` alternative. Other language alternatives are left as they are. Create the `rdf:Alt` structure if missing. |
| Mirrors | If XMP already contains `tiff:ImageDescription` or `exif:UserComment`, set them to the same text. Do not add them if absent. |

- UserComment is written as UTF-16 bytes by the app itself rather than by Exiv2's character conversion, which
  is platform-dependent and fails on emoji. (Exiv2 writes a same-length ASCII placeholder, which is then
  replaced byte-for-byte; for PNG the chunk CRC is recomputed.)
- HEIC/HEIF originals are read-only (D2): the caption goes only into the captioned JPEG copy.
- Missing blocks (no EXIF, no IPTC, no XMP) are created.
- After writing IPTC in a JPEG, update the IPTC digest (8BIM 0x0425) to the MD5 of the new IPTC block
  if a digest resource exists, so Photoshop and MWG-aware tools treat IPTC and XMP as in sync.

### 4.3 Preservation (hard requirements)
- **Pixels are never re-encoded.** For JPEG, the entropy-coded scan data must be byte-identical before
  and after. For PNG, all `IDAT` chunks must be byte-identical.
- Every other metadata field, segment, and chunk is preserved, including MakerNotes (whose internal
  offsets must remain valid), ICC profiles, thumbnails, GPS, and ExtendedXMP.
- The file's modified time and permissions are restored after writing, so "Date modified" sorting and
  backups are not disturbed.
- Because the file is replaced atomically (4.4), its **creation date** becomes the save time, and on macOS
  Finder tags and other extended attributes are not carried over. This is the same trade-off as ExifTool's
  default mode, and is accepted in exchange for never leaving a half-written original.

### 4.4 Safe write procedure
1. Read the original file into memory.
2. Apply the metadata changes in memory.
3. Write the result to a temp file in the same folder (hidden name, non-photo extension, for example
   `.~meta_<random>.tmp`), so the final replace stays on one volume.
4. Verify the new bytes before writing them: the image still decodes, its pixel data is unchanged (4.3),
   every other EXIF, GPS, IPTC and XMP value and the ICC profile are unchanged, and re-reading
   F1 to F4 returns the new caption.
5. Atomically replace the original with the temp file (`os.replace`), then restore the original modified time.
6. On any failure: delete the temp file, leave the original untouched, and show an error.
   If the existing metadata cannot be parsed (for example, corrupt EXIF), refuse to write rather than
   risk losing it. Exiv2 silently drops corrupt EXIF, so this check is done before calling it.

Errors to report clearly: read-only file, file locked by another program, permission denied, network share
disconnected, file changed or missing since it was displayed.

### 4.5 Captioned copy
- The `_captioned` copy carries the same caption in F1 to F4. (HEIC originals produce a JPEG copy, which
  does support IPTC, so all four fields are written there.)
- Caption bar rendering for long captions: one line, shrinking from 32 px to 14 px; then two lines,
  from 20 px down to 10 px; then two lines with the second truncated with "…". The bar stays exactly
  50 px high. The metadata always holds the full text.
- All other metadata rules from section 6 of the main spec still apply (orientation applied and tag removed,
  other EXIF preserved).

### 4.6 Ordering and partial failure
- The metadata write and the captioned copy are independent. If one fails, the other still runs,
  and the status bar reports each result (for example "Caption saved to photo. Captioned copy failed: ...").
- The undo entry covers whatever succeeded.

## 5. Undo
- Before the first write, the original file's full bytes and modified time are backed up to the
  session's temporary undo folder (the same folder used today for prior captioned copies).
- Undo of a caption action:
  - Restores the original photo's previous bytes with the safe write procedure (4.4) and restores its modified time.
  - Restores or removes the captioned copy as it does today.
  - Puts the restored caption back into the caption box.
- Undo is LIFO, so a later YES or NO on the same photo is always undone first.
- Backups are deleted when the folder changes or the app closes (same as today).

## 6. Interaction with existing features
- **YES / NO**: metadata travels with the file. No change.
- **Hide NO / sorting**: not affected. Modified-time sorting is stable because of 4.3.
- **Section 7 of the main spec** ("Never delete or overwrite originals") is amended to:
  "The only file mutations are: move (YES), rename (NO), create or replace a captioned copy,
  **update caption metadata in an original (pixel data never changes)**, and Undo reversals."
- **Length limit**: 2000 bytes of UTF-8 for metadata and the box. The bar shows as much as fits (4.5).

## 7. User interface changes
- Caption box: pre-filled per 3.5; it keeps the text after Submit.
- Amber "⚠ Fields differ" notice (3.4) next to the space-left counter, hidden otherwise. Its tooltip
  names the field being shown and lists every field's value.
- Submit button tooltip: "Save the caption into the photo and create the captioned copy."
- Status messages: "Caption saved to IMG_001.jpg; created IMG_001_captioned.jpg", "Caption removed
  from ...", "Caption already up to date", "Caption edit discarded", plus errors from 4.4.
- HEIC: "HEIC original not changed; created IMG_001_captioned.jpg".

## 8. Implementation

### 8.1 Libraries
- **pyexiv2** (Exiv2 binding, GPL-3.0; acceptable, this is a personal project) reads IPTC and XMP and writes
  all four fields. It is always used on in-memory bytes (`pyexiv2.ImageData`), which avoids Exiv2's
  problems with non-ASCII paths on Windows. Calls are serialized with a lock because Exiv2's XMP toolkit
  is not guaranteed to be thread-safe.
- **Pillow / pillow-heif** read the EXIF fields (raw bytes, so decoding follows 3.2), including from HEIC.
- Exiv2 cannot write HEIC, hence D2.

### 8.2 Code structure
- `photo_review/metadata.py` (no Qt): `read_caption_fields`, `choose_caption`, `apply_caption_to_bytes`,
  `verify_written`, `atomic_write`, `write_caption`.
- `session.save_caption(text, photo=None)` writes the original, creates/replaces/removes the captioned copy,
  and records one undo entry holding backups of both.
- `loader.py` reads `CaptionFields` together with each decoded image. A caption saved while that photo is
  still loading takes precedence over the in-flight read.
- `imaging.create_captioned_copy` embeds the caption metadata in the copy.

## 9. Tests
Automated (`tests/test_metadata.py`, `tests/test_session.py`, `tests/test_imaging.py`, `tests/test_ui.py`):
1. Each field alone is read; blank files and camera placeholders give an empty box.
2. Disagreeing fields: IPTC wins; without IPTC, the order XMP, UserComment, ImageDescription is used.
   NFC and whitespace differences do not count as disagreement.
3. Encodings: UserComment ASCII, UNICODE little- and big-endian (including wrong byte order),
   undefined and JIS prefixes; legacy Latin-1 IPTC; accents, CJK and emoji round-trip.
4. JPEG and PNG writes set all four fields; other XMP languages and keywords are kept; clearing removes them.
5. JPEG scan data and PNG `IDAT` are byte-identical; EXIF camera tags, GPS and ICC profile survive;
   PNG chunk CRCs stay valid.
6. Modified time and permissions are preserved.
7. Read-only file and damaged EXIF: refused, original unchanged, no temp file left.
8. HEIC original never modified; its captioned JPEG copy gets all four fields.
9. Undo restores the original byte-for-byte (and its modified time) and the previous captioned-copy state.
10. Captions up to 2000 bytes accepted; longer refused. Two-line and truncated bar layouts stay inside the bar.
11. UI: pre-fill, save on Next, clear on Next, save before YES, no write when unchanged, Undo discards
    an unsaved edit, the fields-differ notice, and a save during a pending load.
12. When ExifTool is installed: it reads all four fields and `MWG:Description`, big-endian EXIF
    UserComment is written correctly, and the IPTC digest matches after a write.

Manual before release: captions appear in Lightroom Classic, Apple Photos (import), and Windows Explorer
file properties.

## 10. Decisions

| # | Decision | Resolution |
|---|---|---|
| D1 | Allow writing to originals | Yes: metadata only, image data unchanged. |
| D2 | HEIC originals | Read-only. Caption goes only to the captioned JPEG copy. |
| D3 | Captions longer than 50 characters | Allowed up to 2000 bytes (UTF-8). The bar wraps to two lines, then truncates. |
| D4 | Clearing a caption | Supported: clearing the box removes the caption from all fields and deletes the captioned copy. |
| D5 | Unsaved edits when leaving a photo | Saved automatically (4.1). |
| D6 | Separate "save caption only" option | Not added. A save always updates both the metadata and the captioned copy. |
| D7 | pyexiv2 GPL-3.0 licensing | Not an issue (personal project). |
