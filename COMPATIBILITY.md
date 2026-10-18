# Compatibility Findings

## 1. Automated spike (spec 17, step 1)

Run: `python spike/spike.py --samples <real photos>` with ExifTool 13.59 on Linux, 2026-10-18.

Each sample was tested with 15 cases: full write (Rating, Description and mirrors, Subject and
IPTC Keywords, the three date fields set to 1962-06-15), a December 1899 date, ARCHIVE twice
(no duplicate keyword), ARCHIVE then KEEP, clearing the caption, a 3400-byte caption (IPTC copy
truncated to 2000 bytes, XMP kept whole), and 9 hostile strings in caption and tags (leading `-`,
`@`, `<`, quotes, CR/LF newlines, emoji, backslashes, leading/trailing spaces, leading `#`).
For every case it checks: values read back exactly, decoded pixels identical (all TIFF pages),
embedded thumbnails/previews byte-identical, no non-edited tag changed or removed, and the file
modification time preserved.

| Sample | Passed | Failures |
|---|---|---|
| exif_no_xmp.jpg (generated, camera EXIF) | 14/15 | XPComment emoji (see below) |
| lightroom.jpg (generated, Lightroom-style XMP) | 14/15 | XPComment emoji |
| no_metadata.png (generated) | 15/15 | none |
| scan.tif (generated, single page) | 15/15 | none |
| multipage.tif (generated, 3 pages) | 15/15 | none |
| legacy_iptc_latin1.jpg (generated, Latin-1 IPTC, no charset) | 2/15 | existing IPTC text corrupted (see below) |
| iphone.heic (generated with pillow-heif) | 15/15 | none |
| Real Canon EOS 40D JPEG (exif-samples) | 14/15 | XPComment emoji |
| Real iPhone 11 Pro Max HEIC (exif-samples, 2566x3313) | 15/15 | none |

Totals: 119/135. All failures come from findings 3 and 9 below.

### Findings
1. **TIFF writes are safe** (single and multi-page): pixels of all pages unchanged, all values
   round-trip, including 1899 and 1962 dates. TIFF can be treated as writable (spec section 3).
2. **Pre-1970 dates** (1962, 1899) round-trip in JPEG, PNG, TIFF and HEIC in all three date fields.
3. **XPComment cannot hold emoji or other characters outside the Basic Multilingual Plane.**
   ExifTool encodes XP tags as UCS-2 (`Image::ExifTool::Exif`, `Encode($val,"UCS2","II")`), so 😀
   is stored as U+F600. All other fields store emoji correctly. Decision needed (OPEN_QUESTIONS.md Q1).
4. **Hostile strings are safe**: values starting with `-`, `@`, `<`, `#`, with quotes, newlines,
   backslashes and padding round-trip exactly through the argfile (`#[CSTR]` encoding).
5. **IPTC over 2000 bytes**: the app must truncate the IPTC copy itself (done in the spike at a
   UTF-8 character boundary); XMP keeps the full text.
6. **Tags ExifTool adds when creating new blocks** (new tags only, no existing values changed):
   `XMP-x:XMPToolkit`; `IPTC:EnvelopeRecordVersion` (JPEG); for PNG and multi-page TIFF without
   EXIF, the required EXIF tags `ExifVersion`, `ColorSpace`, `ComponentsConfiguration`
   (and `YCbCrPositioning` for PNG).
7. **Housekeeping change**: on the real iPhone HEIC, `XMP-x:XMPToolkit` changed from
   "XMP Core 6.0.0" to "Image::ExifTool 13.59". ExifTool always does this when rewriting XMP.
8. **Structural values move** (expected): TIFF `StripOffsets`, JPEG `ThumbnailOffset`, HEIC
   `MediaDataSize`/`MediaDataOffset`. The image data they point to was verified unchanged.
9. **Writing IPTC as UTF-8 corrupts existing Latin-1 IPTC text.** Older software stored IPTC in
   Latin-1 without declaring a character set. Setting `CodedCharacterSet=UTF8` (spec section 7)
   makes the untouched By-line "Müller" read back as "M?ller" (and City "Zürich" as "Z?rich").
   Every case that writes IPTC failed on that file. Decision needed (OPEN_QUESTIONS.md Q21).

## 2. Manual checks (spec 17, step 2): PENDING, needs Windows and macOS

Files: `spike/out/compatibility_samples.zip` (regenerate with `python spike/spike.py`).
JPEG, HEIC, PNG and TIFF versions of: A dated 2005-08-20; B captioned, 4 tags, dated 1962-06-15
(approximate); C undated; D archived (Rating -1). Plus `jpg_E_keep_with_exif_rating.jpg`
(XMP Rating 1 plus EXIF Rating 1 and RatingPercent 1). File-name order (A, B) is the opposite
of date order (1962, 2005), which reveals how each site sorts.

Record for each file:

| Check | Windows Explorer (Properties > Details) | Apple Photos (after import) | Mixbook | Shutterfly |
|---|---|---|---|---|
| Format accepted | | | | |
| Caption shown (which field?) | | | | |
| Keywords/tags shown | | | | |
| Date taken (1962 correct?) | | | | |
| Rating / stars (1 star for keep?) | | | | |
| Undated file: what date is shown? | | | | |
| Sort order (A before B, or by date?) | | | | |

Decisions that depend on these results:
- Explorer shows no star for `jpg_A..C` but 1 star for `jpg_E` -> also write EXIF Rating and
  RatingPercent on KEEP for JPEG (spec section 6).
- Explorer shows no keywords -> consider XPKeywords (spec section 8).
- Mirror settings defaults (spec section 7).
