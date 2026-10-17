# Caption Metadata: Specification

Status: Draft for review
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
- Captions longer than the box limit: see open decision D3.

## 4. Writing

### 4.1 Trigger
- Pressing **Submit** (or Enter) with non-blank text writes the caption to all applicable fields of the
  **original photo** and creates the captioned copy as it does today (see D1 and D2).
- Metadata is written when the text differs from the displayed caption **or** the fields currently disagree
  (Submit then also syncs them). If all fields already hold this exact text, the original file is not modified.
- Submitting empty text is still an error ("Type a caption first."). Clearing captions is out of scope (D4).
- Navigating away with unsubmitted edits discards them and shows "Caption not saved" in the status bar (D5).

### 4.2 What is written
| Field | Value written |
|---|---|
| F1 | Caption as UTF-8, NUL-terminated (the de facto practice used by ExifTool and Windows). |
| F2 | `ASCII\0\0\0` + text if the caption is pure ASCII. Otherwise `UNICODE\0` + UTF-16 in the EXIF block's byte order, no BOM. |
| F3 | Caption as UTF-8. Set 1:90 to `ESC % G`. If the UTF-8 form is longer than 2000 bytes, refuse (cannot happen with the 50-character limit). |
| F4 | Set the `x-default` alternative. Other language alternatives are left as they are. Create the `rdf:Alt` structure if missing. |
| Mirrors | If XMP already contains `tiff:ImageDescription` or `exif:UserComment`, set them to the same text. Do not add them if absent. |

- HEIC/HEIF: there is no IPTC block, so F3 is not written. See D2 for whether HEIC originals are written at all.
- Missing blocks (no EXIF, no IPTC, no XMP) are created.
- After writing IPTC in a JPEG, update the IPTC digest (8BIM 0x0425) to the MD5 of the new IPTC block
  if a digest resource exists, so Photoshop and MWG-aware tools treat IPTC and XMP as in sync.

### 4.3 Preservation (hard requirements)
- **Pixels are never re-encoded.** For JPEG, the entropy-coded scan data must be byte-identical before
  and after. For PNG, all `IDAT` chunks must be byte-identical.
- Every other metadata field, segment, and chunk is preserved, including MakerNotes (whose internal
  offsets must remain valid), ICC profiles, thumbnails, GPS, and ExtendedXMP.
- The file's modified time is restored after writing, so "Date modified" sorting and backups are not disturbed.
  (The created time is not changed by an in-place rewrite on Windows or macOS.)

### 4.4 Safe write procedure
1. Read the original file into memory.
2. Apply the metadata changes in memory.
3. Write the result to a temp file in the same folder (hidden name, non-photo extension, for example
   `.~meta_<random>.tmp`), so the final replace stays on one volume.
4. Verify the temp file: it decodes as an image, its pixel data is unchanged (4.3), and re-reading
   F1 to F4 returns the new caption.
5. Atomically replace the original with the temp file (`os.replace`), then restore the original modified time.
6. On any failure: delete the temp file, leave the original untouched, and show an error.
   If the existing metadata cannot be parsed, refuse to write rather than risk damaging the file.

Errors to report clearly: read-only file, file locked by another program, permission denied, network share
disconnected, file changed or missing since it was displayed.

### 4.5 Captioned copy
- The `_captioned` copy carries the same caption in F1 to F4. (HEIC originals produce a JPEG copy, which
  does support IPTC, so all four fields are written there.)
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
- **Character limit**: the 50-character limit continues to apply to new or edited captions.

## 7. User interface changes
- Caption box: pre-filled per 3.5.
- Amber "fields disagree" notice (3.4) next to the remaining-character count, hidden otherwise.
- Submit button tooltip: "Save this caption into the photo and create a captioned copy."
- Status messages: "Caption saved to IMG_001.jpg", "Caption already up to date", plus errors from 4.4.
- HEIC (if D2 is read-only): status "Caption saved to the captioned copy only. HEIC originals are not modified."

## 8. Implementation guidance

### 8.1 Library choice
| Option | Pros | Cons |
|---|---|---|
| **pyexiv2** (Exiv2 binding), recommended | Handles EXIF, IPTC, and XMP in JPEG and PNG correctly, including MakerNote offsets and IPTC charset. Can work on in-memory bytes (`pyexiv2.ImageData`), which also avoids Exiv2's known problems with non-ASCII paths on Windows. | GPL-3.0 license: acceptable for internal use, needs legal review before distributing outside the company. Adds a native library to the PyInstaller bundle. Exiv2 cannot write HEIC. Verify wheels exist for the target Python version on Windows x64 and macOS arm64/x86_64. |
| ExifTool (bundled executable) | The reference implementation. Writes HEIC. | Ships a Perl runtime (large, Windows) or depends on system Perl (macOS). Runs as a subprocess. |
| In-house segment writer | No new dependencies or licensing questions. | Writing EXIF without breaking MakerNotes is difficult and risky. Significant testing burden. |

HEIC reading: use pillow-heif (already a dependency), which exposes the EXIF and XMP items.

### 8.2 Code structure
- New module `photo_review/metadata.py` with no Qt dependency:
  - `read_caption_fields(path) -> CaptionFields` (four raw normalized values plus any read errors).
  - `choose_caption(fields) -> tuple[str, str | None, bool]` (text, chosen field, fields-disagree flag).
  - `write_caption(path, text) -> None`, implementing 4.2 to 4.4; raises `FileOpError`.
- `session.add_caption()` calls `write_caption()` on the original, then creates the copy, and records one undo entry.
- `loader.py` returns `CaptionFields` with each decoded image.

## 9. Tests
Unit tests with generated fixture files (no copyrighted samples):
1. Reading each field alone (JPEG, PNG, HEIC where applicable) shows that value.
2. All fields equal shows that value. All blank or placeholders (`OLYMPUS DIGITAL CAMERA`, spaces) shows an empty box.
3. Disagreeing fields with IPTC present shows IPTC. Without IPTC, the order XMP, UserComment, ImageDescription is used.
4. Encodings: UserComment in ASCII, UNICODE little-endian, UNICODE big-endian, and undefined prefixes;
   IPTC with and without 1:90; non-Latin text (accents, CJK, emoji) round-trips.
5. Writing sets all applicable fields; HEIC skips IPTC (or is refused, per D2).
6. JPEG scan data and PNG `IDAT` are byte-identical after writing; ICC profile, GPS, and MakerNote survive
   (compare a MakerNote tag value before and after).
7. File modified time is unchanged after writing.
8. Read-only file: error, original unchanged, no temp file left behind.
9. Unparseable existing metadata: write refused, original unchanged.
10. Undo restores the original byte-for-byte and the previous captioned-copy state.
11. Captioned copy contains the caption in all four fields.
12. Submitting text identical to all four fields does not modify the original.
13. Interoperability check (manual, before release): captions written by the app appear in Lightroom Classic,
    Apple Photos (import), Windows Explorer file properties, and ExifTool `-MWG:Description`.

## 10. Open decisions

| # | Decision | Recommended default |
|---|---|---|
| D1 | Writing to originals changes the main spec's rule that originals are never modified. Approve the amendment in section 6? | Approve: metadata only, pixels untouched, atomic write, full undo. |
| D2 | HEIC originals: Exiv2 cannot write HEIC. Options: (a) read-only, caption goes only to the captioned JPEG copy; (b) bundle ExifTool for HEIC writes; (c) write an XMP sidecar (`IMG_1234.xmp`, read by Lightroom but not by Apple Photos). | (a) for v1. Revisit (b) if HEIC captions matter. |
| D3 | Existing captions longer than 50 characters. Options: (a) show the full text in the box and allow saving up to 2000 bytes, while the caption bar shrinks or truncates the text; (b) show it truncated with a warning, and Submit replaces it with the shorter text. | (a): never silently shorten existing data. The 50-character limit then applies only to the bar rendering. |
| D4 | Clearing a caption (removing it from all fields). | Not in v1. |
| D5 | Unsaved edits when navigating away: discard with a status message, or auto-save. | Discard with a status message: no surprise writes to originals. |
| D6 | Should Submit always create the captioned copy, or add a "Save caption only" option (metadata without the bar)? | Add a "Also create captioned copy" checkbox, on by default, remembered in config. |
| D7 | pyexiv2 GPL-3.0 licensing for the bundled app. | Internal distribution only; confirm with legal before any external distribution. |
