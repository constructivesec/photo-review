# Open Questions

Status: all 22 resolved by the owner on 2026-10-18. Answers are recorded under "Decisions";
the original questions follow for reference. New ambiguities are added at the end.

## Decisions
| # | Decision |
|---|---|
| 1 | Bundle an ExifTool config that redefines XPComment as UTF-16 (surrogate pairs). Verified: emoji round-trip and the stored bytes are UTF-16LE. |
| 2 | Owner supplied own samples (iPhone HEIC; scans as JPG/PNG). Not committed until the owner confirms, because the repository is public. |
| 3 | KEEP never changes ratings 2 to 5, in either view. |
| 4 | Date taken: oldest first only, undated last. Rating: highest first. Ties by filename. |
| 5 | Ignore timezone offsets when comparing and displaying; never write offsets. |
| 6 | As written: only EXIF and XMP-exif DateTimeOriginal count. |
| 7 | Reject a month later than the current month in the current year. |
| 8 | As written. |
| 9 | Mirror off: legacy caption fields are left untouched. |
| 10 | Keywords from other apps are left untouched: shown as read-only chips, never modified or removed. |
| 11 | Tags limited to 64 bytes of UTF-8. |
| 12 | Case-insensitive duplicates keep the XMP spelling. |
| 13 | Export settings in the export dialog; a small Settings dialog for "Mirror to legacy fields" and "Also set CreateDate". |
| 14 | Export copies JPEGs byte-for-byte when no change is needed (upright, sRGB, no burned caption). |
| 15 | Prefix off: name collisions get a numeric suffix, listed in the summary. |
| 16 | Non-empty output folder: "Choose another folder" or "Cancel". |
| 17 | captions.csv tags column `; `-separated, excluding `status:` and `date:` tags. |
| 18 | Photos that fail to export are skipped and listed in the summary. |
| 19 | PNG with an embedded profile is converted to sRGB on export, like JPEG. |
| 20 | Changed-on-disk check fails: reload, tell the user, cancel the action. |
| 21 | Legacy Latin-1 IPTC is re-written as UTF-8 in the same call (`-charset iptc=Latin -tagsFromFile @ -IPTC:all`, excluding the edited IPTC tags). Verified. |
| 22 | macOS uses the system Perl with the bundled Image-ExifTool; startup check with a clear error. |

## Original questions

Ambiguities found in the specification, each with a recommended answer. Reply with answers,
or "use recommendations" for any you agree with. Work that depends on an open question is on hold.

## From the spike
(Q21 below is also a spike finding.)

1. **XPComment and emoji.** ExifTool stores XPComment as UCS-2, so emoji become garbage in that
   one field (COMPATIBILITY.md finding 3). Options: (a) bundle a small ExifTool config that
   redefines XPComment as UTF-16 with surrogate pairs (what Windows itself writes); (b) write
   XPComment with those characters replaced by U+FFFD; (c) skip XPComment when the caption has them.
   *Recommended: (a), verified by the spike and by Explorer in the manual check.*
2. **Real sample files for tests.** The spike used a real iPhone HEIC and Canon JPEG from a public
   repository with no stated license, so they are not committed. Can you supply a real iPhone HEIC
   and a real flatbed-scanner JPEG that may be committed as test fixtures?
   *Recommended: yes; until then, tests use generated stand-ins.*

## Ratings and sorting
3. **KEEP from the archived view on a file rated 2 to 5** (for example, moved there by hand): set it
   to 1 as section 5.5 says, or leave 2 to 5 unchanged as section 5.3 does? *Recommended: leave 2 to 5 unchanged.*
4. **Sort directions.** "Date taken" and "Rating" have no direction. *Recommended: Date taken oldest
   first and newest first (undated always last in both); Rating highest first; ties by filename.*
5. **Timezones in date comparison.** XMP dates may carry an offset (iPhone: `-05:00`), EXIF does not.
   *Recommended: compare and display date and time only, ignoring offsets; never write offsets.*
6. **Dates in other fields.** Photos whose only date is in XMP-photoshop:DateCreated or
   IPTC:DateCreated count as undated under section 9. Is that intended? *Recommended: yes, as written.*
7. **Future dates.** Year range is 1826 to the current year. Reject a month later than this month
   in the current year? *Recommended: yes.*

## Captions and tags
8. **Legacy caption shown as placeholder.** If XMP Description is empty but EXIF ImageDescription
   has camera text (for example "OLYMPUS DIGITAL CAMERA"), it appears as placeholder text. Saving an
   empty caption then clears that EXIF field too. Intended? *Recommended: yes, as written.*
9. **"Mirror to legacy fields" turned off.** Leave existing legacy captions untouched (they may go
   stale) or clear them? *Recommended: leave untouched.*
10. **Keywords from other apps** that break the tag rules (for example "Lightroom Keyword" with a
    space, "Christmas" capitalized): show them as removable chips, or read-only? *Recommended:
    show as chips the user can remove, never rewritten or re-cased.*
11. **IPTC Keywords are limited to 64 bytes.** A 64-character tag with accents exceeds that.
    *Recommended: limit tags to 64 bytes of UTF-8 (not 64 characters).*
12. **Case-insensitive dedupe:** when XMP has "Emma" and IPTC has "emma", keep which?
    *Recommended: the XMP spelling.*

## Settings
13. **Where are settings changed?** Config holds "Mirror to legacy fields", "Also set CreateDate",
    "Prefix export with sequence numbers" and "Caption bar height percent", but the menu has no
    Settings item, and "do not add features" applies. *Recommended: put the two export settings in
    the export dialog and add a small Settings dialog for the two metadata settings (needs your OK
    as an addition).*

## Export
14. **Re-encoding JPEGs.** "JPEG stays JPEG ... quality 95, orientation applied" implies every JPEG
    is decoded and re-encoded (small quality loss). Copy JPEGs byte-for-byte when no change is
    needed (upright, sRGB, no burned caption)? *Recommended: yes, copy unchanged when possible.*
15. **Name collisions when the sequence prefix is off** (IMG_1.heic and IMG_1.jpg both become
    IMG_1.jpg). *Recommended: numeric suffix (IMG_1_1.jpg), listed in the summary.*
16. **Non-empty output folder.** "Stop and ask": what choices? *Recommended: "Choose another
    folder" or "Cancel" only (no merging into a non-empty folder).*
17. **captions.csv tags column.** Separator, and include system tags? *Recommended: `; `-separated,
    excluding `status:` and `date:` tags.*
18. **Photos that fail to export** (corrupt, unreadable). *Recommended: skip, continue, list in the summary.*
19. **sRGB for PNG.** Convert PNG with an embedded profile to sRGB too? *Recommended: yes, same as JPEG.*

## Safety and behaviour
20. **Changed-on-disk check.** If size/mtime changed since load: reload, tell the user, and cancel
    the action (user presses the key again)? *Recommended: yes, cancel.*
21. **IPTC character set on legacy JPEGs (confirmed by the spike, COMPATIBILITY.md finding 9).**
    Writing `CodedCharacterSet=UTF8` to a JPEG whose existing IPTC is Latin-1 corrupts its other IPTC
    text ("Müller" becomes "M?ller"). *Recommended: when a JPEG has IPTC text but no UTF-8 declaration,
    re-write all its existing IPTC text fields as UTF-8 in the same ExifTool call (read with
    `-charset iptc=Latin`, write back with UTF-8), so nothing is lost; covered by the spike fixture.*
22. **macOS ExifTool runtime.** Use the system Perl (`/usr/bin/perl`, present on current macOS
    though Apple has marked scripting runtimes deprecated) with the bundled Image-ExifTool, rather
    than bundling Perl. *Recommended: system Perl, with a startup check and a clear error if missing.*

## New questions (2026-10-18, from the owner's test pictures)

23. **"Scan?" hint for the owner's slide scanner.** Its files say Make "MAKER NAME", Model "96660"
    (a Novatek-based slide scanner), which matches none of the scanner terms in spec section 4, so no
    hint is shown. Add "MAKER NAME" and "NOVATEK" to the scanner terms? *Recommended: yes.*
24. **Camera placeholder captions.** These scans have ImageDescription "NOVATEK CAMERA", which is shown
    as grey placeholder caption text (Q8: as written). Treat known camera placeholders ("NOVATEK CAMERA",
    "OLYMPUS DIGITAL CAMERA", "SONY DSC", ...) as blank? *Recommended: yes, display only; files untouched.*
25. **Scan dates look like real dates.** The slide scanner stores the 2017 scan date as
    DateTimeOriginal, so these photos are "dated" (they sort under 2017 and do not appear in
    "Show undated only"). As written, the user fixes each date by hand. Alternative: when the device
    matches a scanner term, treat the date as undated for sorting and the filter (files untouched).
    *Recommended: keep as written for now; decide after you try it.*
26. **Committing your test pictures.** The repository is public. May the six files from the zip be
    committed under `tests/fixtures/real/` so CI tests them on Windows and macOS? They contain no GPS,
    but they are personal photos (and the Facebook file name contains a Facebook photo ID).
    *Recommended: only if you are comfortable with them being public; otherwise keep them local.*
