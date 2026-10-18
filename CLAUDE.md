# Photo Library Curation App

Spec: the "Photo Library Curation App: Specification" provided by the owner is the source of truth.
Do not add features outside it. Unresolved ambiguities are tracked in `OPEN_QUESTIONS.md`; ask, don't guess.

## Stack
- Python 3.12+ (PySide6 6.12.0 crashes on 3.11; COMPATIBILITY.md finding 14), PySide6 GUI. Pillow + pillow-heif for display and export only.
- All EXIF/XMP/IPTC reads and writes go through ExifTool (pinned 13.59, fetched and
  checksum-verified by `tools/fetch_exiftool.py` into `vendor/exiftool/`) via PyExifTool,
  one persistent process (`photo_review/metadata.py`).
- Modules (`photo_review/`): ui, file_ops, metadata, date_logic, tag_logic, export, config.
- Dependencies pinned exactly in `requirements*.txt`. Type hints everywhere; ruff for lint and format.

## Rules
- Never alter pixel data outside export. Never re-encode, convert format, or write sidecars for originals.
- No silent fallbacks: if a write fails, show an error and leave the file untouched.
- ExifTool writes: one call per action, `-overwrite_original -P`, arguments via argfile only
  (never `shell=True`); values with newlines/backslashes/edge spaces use `#[CSTR]` lines (`encode_arg`).
- File mutations are limited to moves between the active folder and `_archive`, and metadata writes.
- Show passing test output before declaring a milestone complete.

## Commands
```bash
pip install -r requirements-dev.txt
python tools/fetch_exiftool.py                  # pinned ExifTool into vendor/ (add --platform windows on Windows builds)
python spike/spike.py --samples DIR             # metadata spike; report in spike/out/report.md
python tools/make_fixtures.py OUT_DIR           # sample folder for manual testing
ruff check . && ruff format --check .           # lint
python -m pytest                                # tests (use the real bundled ExifTool)
python -m photo_review                          # run (once the UI exists)
pyinstaller --noconfirm --clean photo_review.spec   # build (see BUILD.md, once packaging exists)
```
