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

**First run: 119/135.** Two real failures (findings 3 and 9), both resolved by decisions Q1 and Q21.

**Current run, with both fixes: 210/210** (2026-10-18, owner's original files from the test zip).

| Sample | Passed |
|---|---|
| exif_no_xmp.jpg, lightroom.jpg, no_metadata.png, scan.tif, multipage.tif, legacy_iptc_latin1.jpg, iphone.heic (generated) | 15/15 each |
| Real Canon EOS 40D JPEG (public sample) | 15/15 |
| Real iPhone 11 Pro Max HEIC (public sample) | 15/15 |
| Owner: iPhone 11 HEIC (4032x3024, Display P3, rotated 90°) | 15/15 |
| Owner: slide scans PICT0268/PICT0389 (5760x3840 JPEG, slide scanner) | 15/15 each |
| Owner: Facebook download JPEG (undeclared IPTC, "c2" colour profile) | 15/15 |
| Owner: flatbed scan PNG (1632x2520, no metadata) | 15/15 |

### Findings
1. **TIFF writes are safe** (single and multi-page): pixels of all pages unchanged, all values
   round-trip, including 1899 and 1962 dates. TIFF can be treated as writable (spec section 3).
2. **Pre-1970 dates** (1962, 1899) round-trip in JPEG, PNG, TIFF and HEIC in all three date fields.
3. **XPComment cannot hold emoji or other characters outside the Basic Multilingual Plane.**
   ExifTool encodes XP tags as UCS-2 (`Image::ExifTool::Exif`, `Encode($val,"UCS2","II")`), so 😀
   is stored as U+F600. **Resolved (Q1):** `photo_review/resources/exiftool_config.pl` redefines
   XPComment as UTF-16 with surrogate pairs; the stored bytes are verified to be UTF-16LE.
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
   Every case that writes IPTC failed on that file. **Resolved (Q21):** existing IPTC text is
   re-written as UTF-8 in the same call (`-charset iptc=Latin -tagsFromFile @ -IPTC:all`,
   excluding the IPTC tags being edited).
10. **`-P` keeps the modification time only to the second.** The app restores the exact
    (nanosecond) modification time after each write.
11. **ExifTool JSON emits number-like text unquoted** (a caption "1.50" would read back as 1.5).
    The app parses numbers as their exact text.
12. **Owner's slide scanner** writes Make "MAKER NAME", Model "96660", ImageDescription
    "NOVATEK CAMERA", and the **scan** date as DateTimeOriginal (2017). See OPEN_QUESTIONS.md Q23-Q25.
13. **Facebook's "c2" colour profile** is sRGB under another name (primaries and white point match
    sRGB within 0.0002). Export now recognises sRGB by primaries, so these JPEGs are copied unchanged.


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
