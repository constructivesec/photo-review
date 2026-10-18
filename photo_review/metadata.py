"""ExifTool wrapper and metadata field map. All EXIF, XMP and IPTC reads and writes go through here.

One persistent ExifTool process (-stay_open) is used, owned by a single worker thread.
Arguments are sent through ExifTool's argument file on stdin, one per line, never through a
shell. Arguments containing characters that would break that format (newlines, backslashes,
tabs, leading or trailing spaces, a leading '#') are sent as "#[CSTR]" C-string lines.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import exiftool  # PyExifTool

from . import date_logic, tag_logic

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
RESOURCES = Path(getattr(sys, "_MEIPASS", ROOT)) / "photo_review" / "resources"
CONFIG_FILE = RESOURCES / "exiftool_config.pl"

WRITE_FLAGS: tuple[str, ...] = ("-overwrite_original", "-P")
COMMON_ARGS: tuple[str, ...] = ("-charset", "filename=utf8")
IPTC_CAPTION_MAX = 2000  # bytes
IPTC_KEYWORD_MAX = 64  # bytes

_CSTR_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\a": "\\a", "\b": "\\b", "\f": "\\f"}

# Tag names as ExifTool reports them with -G1.
RATING = "XMP-xmp:Rating"
DESCRIPTION = "XMP-dc:Description"
IMAGE_DESCRIPTION = "IFD0:ImageDescription"
IPTC_CAPTION = "IPTC:Caption-Abstract"
XP_COMMENT = "IFD0:XPComment"
SUBJECT = "XMP-dc:Subject"
IPTC_KEYWORDS = "IPTC:Keywords"
IPTC_CHARSET = "IPTC:CodedCharacterSet"
EXIF_DTO = "ExifIFD:DateTimeOriginal"
XMP_DTO = "XMP-exif:DateTimeOriginal"
PS_DATE_CREATED = "XMP-photoshop:DateCreated"
EXIF_CREATE = "ExifIFD:CreateDate"
XMP_CREATE = "XMP-xmp:CreateDate"

READ_TAGS: tuple[str, ...] = (
    "File:FileType",
    f"{RATING}#",
    DESCRIPTION,
    IMAGE_DESCRIPTION,
    IPTC_CAPTION,
    XP_COMMENT,
    SUBJECT,
    IPTC_KEYWORDS,
    EXIF_DTO,
    XMP_DTO,
    PS_DATE_CREATED,
    EXIF_CREATE,
    XMP_CREATE,
    "IFD0:Make",
    "IFD0:Model",
    "Composite:GPSLatitude#",
    "Composite:GPSLongitude#",
    "ExifIFD:ExposureTime",
    "ExifIFD:FNumber",
    "ExifIFD:ISO",
    "ExifIFD:FocalLength",
    "IPTC:all",
)
SYSTEM_PREFIXES = ("System:", "SourceFile", "ExifTool:")


class MetadataError(Exception):
    """A user-facing metadata read or write failure."""


# ---------------------------------------------------------------- process


def find_exiftool() -> Path:
    """Locate the bundled ExifTool (frozen app, then vendor/, then $PHOTO_REVIEW_EXIFTOOL)."""
    override = os.environ.get("PHOTO_REVIEW_EXIFTOOL")
    if override:
        return Path(override)
    name = "exiftool.exe" if sys.platform == "win32" else "exiftool"
    sub = "windows" if sys.platform == "win32" else "unix"
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "exiftool" / name)
    candidates.append(ROOT / "vendor" / "exiftool" / sub / name)
    for c in candidates:
        if c.is_file():
            return c
    raise MetadataError(
        "ExifTool not found. Run: python tools/fetch_exiftool.py "
        f"(looked in: {', '.join(str(c) for c in candidates)})"
    )


def check_perl() -> str | None:
    """On macOS/Linux the bundled ExifTool runs on the system Perl. Return an error or None."""
    if sys.platform == "win32":
        return None
    for p in ("/usr/bin/perl", "/usr/local/bin/perl", "/opt/homebrew/bin/perl"):
        if os.access(p, os.X_OK):
            return None
    return "Perl was not found (/usr/bin/perl). ExifTool needs Perl to run on this computer."


def encode_arg(arg: str) -> str:
    """Encode one argument for ExifTool's line-based argument file."""
    for ch in arg:
        if (ord(ch) < 0x20 and ch not in _CSTR_ESCAPES) or ord(ch) == 0x7F:
            raise MetadataError(f"Text contains an unsupported control character (U+{ord(ch):04X}).")
    needs_cstr = any(ch in arg for ch in _CSTR_ESCAPES) or arg != arg.strip() or arg.startswith("#")
    if not needs_cstr:
        return arg
    out = []
    for ch in arg:
        out.append(_CSTR_ESCAPES.get(ch, ch))
    return "#[CSTR]" + "".join(out)


def loads(text: str) -> list[dict]:
    """Parse ExifTool JSON keeping numbers as their exact text (a caption "1.50" stays "1.50")."""
    return json.loads(text, parse_float=str, parse_int=str) if text.strip() else []


class ExifToolSession:
    """A single persistent ExifTool process. Use from one worker thread only."""

    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable or find_exiftool()
        self._et = exiftool.ExifTool(
            executable=str(self.executable),
            common_args=list(COMMON_ARGS),
            config_file=str(CONFIG_FILE),
            encoding="utf-8",
        )
        self._lock = threading.Lock()  # guards against accidental cross-thread use
        self.last_warnings = ""

    def __enter__(self) -> ExifToolSession:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        if not self._et.running:
            self._et.run()

    def stop(self) -> None:
        if self._et.running:
            try:
                self._et.terminate()
            except Exception:  # noqa: BLE001 - process already gone
                pass

    @property
    def version(self) -> str:
        return self.execute("-ver").strip()

    def _run(self, args: Sequence[str], raw: bool) -> str | bytes:
        encoded = [encode_arg(a) for a in args]
        with self._lock:
            self.start()
            try:
                out = self._et.execute(*encoded, raw_bytes=raw)
            except Exception as exc:  # process died, I/O error
                self.stop()
                raise MetadataError(f"ExifTool stopped unexpectedly: {exc}") from exc
            status, err = self._et.last_status, (self._et.last_stderr or "").strip()
        self.last_warnings = err
        if status != 0:
            raise MetadataError(err or f"ExifTool exited with status {status}")
        return out

    def execute(self, *args: str) -> str:
        out = self._run(args, raw=False)
        assert isinstance(out, str)
        return out

    def execute_bytes(self, *args: str) -> bytes:
        out = self._run(args, raw=True)
        assert isinstance(out, bytes)
        return out

    def read_json(
        self,
        paths: Sequence[Path | str],
        tags: Iterable[str] = (),
        extra: Iterable[str] = (),
        numeric: bool = False,
    ) -> list[dict]:
        """Read tags (all if none given) as JSON keyed "Group1:Tag" (-G1).

        Display values unless numeric=True; append "#" to one tag name for its raw value.
        Files that cannot be read are reported in the result with an "Error" key by ExifTool,
        or omitted; callers match results by SourceFile.
        """
        if not paths:
            return []
        args = [
            "-j",
            "-G1",
            "-struct",
            *(["-n"] if numeric else []),
            *extra,
            *(f"-{t}" for t in tags),
            *(str(p) for p in paths),
        ]
        try:
            out = self.execute(*args)
        except MetadataError:
            # ExifTool exits non-zero if any file in the batch failed; output is still valid.
            out = self._et.last_stdout or ""
        return loads(out)

    def write(self, path: Path | str, args: Sequence[str]) -> str:
        """Write all assignments to one file in one ExifTool call (-overwrite_original -P)."""
        before = os.stat(path)
        try:
            out = self.execute(*WRITE_FLAGS, *args, str(path))
        except MetadataError as exc:
            raise MetadataError(redact(str(exc), _values(args))) from None
        if self.last_warnings:
            log.info("ExifTool: %s (%s)", redact(self.last_warnings, _values(args)), path)
        if "1 image files updated" not in out and "1 image files unchanged" not in out:
            raise MetadataError(out.strip() or "ExifTool did not update the file")
        # -P keeps the modification time to the second; restore the exact value.
        try:
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        except OSError:
            pass
        return out


# ---------------------------------------------------------------- reading


@dataclass(frozen=True)
class FileStamp:
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> FileStamp:
        st = path.stat()
        return cls(st.st_size, st.st_mtime_ns)


def _as_list(v: object) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _as_text(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


@dataclass
class PhotoMeta:
    """Metadata of one photo as read by ExifTool. Raw values keep ExifTool's display form."""

    path: Path
    stamp: FileStamp | None
    raw: dict[str, object] = field(default_factory=dict)
    error: str | None = None

    def get(self, tag: str) -> str | None:
        return _as_text(self.raw.get(tag))

    @property
    def file_type(self) -> str:
        return str(self.raw.get("File:FileType") or "")

    @property
    def is_jpeg(self) -> bool:
        return self.file_type == "JPEG"

    @property
    def rating(self) -> int | None:
        v = self.raw.get(RATING)
        try:
            return int(float(str(v))) if v not in (None, "") else None
        except ValueError:
            return None

    @property
    def subject(self) -> list[str]:
        return _as_list(self.raw.get(SUBJECT))

    @property
    def iptc_keywords(self) -> list[str]:
        return _as_list(self.raw.get(IPTC_KEYWORDS))

    @property
    def keywords(self) -> list[str]:
        """Union of XMP Subject and IPTC Keywords, deduped case-insensitively (XMP spelling wins)."""
        return tag_logic.merge_keywords(self.subject, self.iptc_keywords)

    @property
    def is_archived(self) -> bool:
        return self.rating == -1

    @property
    def iptc_present(self) -> bool:
        return any(k.startswith("IPTC:") for k in self.raw)

    @property
    def iptc_is_utf8(self) -> bool:
        v = self.raw.get(IPTC_CHARSET)
        return v in ("UTF8", "\x1b%G")

    @property
    def caption(self) -> str:
        """The editable caption (XMP-dc:Description)."""
        return self.get(DESCRIPTION) or ""

    @property
    def caption_placeholder(self) -> str:
        """Read-only fallback shown when XMP is empty: EXIF ImageDescription, then IPTC."""
        if self.caption:
            return ""
        for tag in (IMAGE_DESCRIPTION, IPTC_CAPTION):
            v = (self.get(tag) or "").strip()
            if v:
                return v
        return ""

    @property
    def date(self) -> date_logic.EffectiveDate:
        return date_logic.effective_date(self.get(EXIF_DTO), self.get(XMP_DTO))

    @property
    def approximate(self) -> bool:
        return any(k.casefold() == tag_logic.APPROXIMATE for k in self.keywords)

    @property
    def gps(self) -> tuple[float, float] | None:
        try:
            lat, lon = self.raw.get("Composite:GPSLatitude"), self.raw.get("Composite:GPSLongitude")
            if lat in (None, "") or lon in (None, ""):
                return None
            return float(str(lat)), float(str(lon))
        except ValueError:
            return None


def read_meta(et: ExifToolSession, paths: Sequence[Path]) -> dict[Path, PhotoMeta]:
    """Read the field map for many files in one ExifTool call. Unreadable metadata is empty."""
    result: dict[Path, PhotoMeta] = {}
    stamps: dict[Path, FileStamp | None] = {}
    for p in paths:
        try:
            stamps[p] = FileStamp.of(p)
        except OSError:
            stamps[p] = None
    readable = [p for p in paths if stamps[p] is not None]
    by_source: dict[str, dict] = {}
    for item in et.read_json(readable, READ_TAGS):
        by_source[os.path.normcase(os.path.abspath(str(item.get("SourceFile", ""))))] = item
    for p in paths:
        item = by_source.get(os.path.normcase(os.path.abspath(str(p))))
        if stamps[p] is None:
            result[p] = PhotoMeta(p, None, error="File not found")
        elif item is None:
            result[p] = PhotoMeta(p, stamps[p], error="Metadata could not be read")
        else:
            raw = {k: v for k, v in item.items() if not k.startswith(SYSTEM_PREFIXES)}
            err = raw.pop("ExifTool:Error", None) or item.get("ExifTool:Error")
            result[p] = PhotoMeta(p, stamps[p], raw=raw, error=str(err) if err else None)
    return result


# ---------------------------------------------------------------- write builders
#
# Each builder returns the arguments for ONE ExifTool call, plus the tags it changes so the
# caller can snapshot their previous values for undo or rollback.


@dataclass
class WritePlan:
    args: list[str]
    tags: tuple[str, ...]  # tags whose previous values must be restored on undo
    notes: list[str] = field(default_factory=list)  # user-facing status notes

    @property
    def empty(self) -> bool:
        return not self.args


def _truncate_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _iptc_prefix(meta: PhotoMeta, edited_iptc: Sequence[str]) -> list[str]:
    """Declare IPTC as UTF-8. Legacy (non-UTF-8) IPTC text is converted in the same call."""
    args: list[str] = []
    if meta.iptc_present and not meta.iptc_is_utf8:
        args += ["-charset", "iptc=Latin", "-tagsFromFile", "@", "-IPTC:all"]
        args += [f"--{t}" for t in (*edited_iptc, IPTC_CHARSET)]
    args.append(f"-{IPTC_CHARSET}=UTF8")
    return args


def _list_args(tag: str, values: Sequence[str]) -> list[str]:
    return [f"-{tag}={v}" for v in values] if values else [f"-{tag}="]


def plan_keep(meta: PhotoMeta) -> WritePlan:
    """Rating 1 only if absent, 0 or -1 (2-5 untouched); remove status:archive."""
    args: list[str] = []
    if meta.rating in (None, 0, -1):
        args.append(f"-{RATING}=1")
    if any(k.casefold() == tag_logic.ARCHIVE for k in meta.subject):
        args.append(f"-{SUBJECT}-={tag_logic.ARCHIVE}")
    tags: list[str] = [RATING, SUBJECT]
    if meta.is_jpeg and any(k.casefold() == tag_logic.ARCHIVE for k in meta.iptc_keywords):
        remaining = [k for k in meta.iptc_keywords if k.casefold() != tag_logic.ARCHIVE]
        args += _iptc_prefix(meta, [IPTC_KEYWORDS]) + _list_args(IPTC_KEYWORDS, remaining)
        tags.append(IPTC_KEYWORDS)
    return WritePlan(args, tuple(tags))


def plan_archive(meta: PhotoMeta) -> WritePlan:
    """Rating -1 and status:archive in Subject (never duplicated)."""
    return WritePlan(
        [f"-{RATING}=-1", f"-{SUBJECT}-={tag_logic.ARCHIVE}", f"-{SUBJECT}+={tag_logic.ARCHIVE}"],
        (RATING, SUBJECT),
    )


def plan_caption(meta: PhotoMeta, text: str, mirror: bool) -> WritePlan:
    """XMP Description; with mirror on also ImageDescription, and for JPEG IPTC and XPComment.

    An empty caption clears every field written.
    """
    args = [f"-{DESCRIPTION}={text}"]
    tags: list[str] = [DESCRIPTION]
    notes: list[str] = []
    if mirror:
        args.append(f"-{IMAGE_DESCRIPTION}={text}")
        tags.append(IMAGE_DESCRIPTION)
        if meta.is_jpeg:
            iptc = _truncate_bytes(text, IPTC_CAPTION_MAX)
            if iptc != text:
                notes.append(
                    f"The IPTC copy of the caption was shortened to {IPTC_CAPTION_MAX} bytes "
                    "(the full caption is kept in XMP)."
                )
            args += _iptc_prefix(meta, [IPTC_CAPTION]) + [f"-{IPTC_CAPTION}={iptc}"]
            args.append(f"-{XP_COMMENT}={text}")
            tags += [IPTC_CAPTION, XP_COMMENT]
    return WritePlan(args, tuple(tags), notes)


def plan_keywords(meta: PhotoMeta, keywords: Sequence[str]) -> WritePlan:
    """Write the full merged keyword list to XMP Subject and (JPEG) IPTC Keywords."""
    args = _list_args(SUBJECT, keywords)
    tags: list[str] = [SUBJECT]
    if meta.is_jpeg:
        iptc = [_truncate_bytes(k, IPTC_KEYWORD_MAX) for k in keywords]
        args += _iptc_prefix(meta, [IPTC_KEYWORDS]) + _list_args(IPTC_KEYWORDS, iptc)
        tags.append(IPTC_KEYWORDS)
    return WritePlan(args, tuple(tags))


def plan_date(meta: PhotoMeta, value: str, approximate: bool, set_create_date: bool) -> WritePlan:
    """Write the date to the three date fields (and CreateDate if enabled), set the approx flag."""
    date_tags = [EXIF_DTO, XMP_DTO, PS_DATE_CREATED] + ([EXIF_CREATE, XMP_CREATE] if set_create_date else [])
    args = [f"-{t}={value}" for t in date_tags]
    tags = list(date_tags)
    flag = tag_logic.APPROXIMATE
    in_xmp = any(k.casefold() == flag for k in meta.subject)
    if approximate and not in_xmp:
        args.append(f"-{SUBJECT}+={flag}")
    if not approximate:
        args += [f"-{SUBJECT}-={k}" for k in meta.subject if k.casefold() == flag]
        if meta.is_jpeg and any(k.casefold() == flag for k in meta.iptc_keywords):
            remaining = [k for k in meta.iptc_keywords if k.casefold() != flag]
            args += _iptc_prefix(meta, [IPTC_KEYWORDS]) + _list_args(IPTC_KEYWORDS, remaining)
            tags.append(IPTC_KEYWORDS)
    tags.append(SUBJECT)
    return WritePlan(args, tuple(tags))


# ---------------------------------------------------------------- undo / rollback

Snapshot = dict[str, object]
_DATE_TAGS = {EXIF_DTO, XMP_DTO, PS_DATE_CREATED, EXIF_CREATE, XMP_CREATE}


def snapshot(meta: PhotoMeta, tags: Iterable[str]) -> Snapshot:
    """Previous values of the given tags (None = absent), for restore_plan()."""
    return {t: meta.raw.get(t) for t in tags}


def restore_plan(meta: PhotoMeta, snap: Snapshot) -> WritePlan:
    """Arguments that put every snapshotted tag back exactly as it was (absent tags deleted)."""
    args: list[str] = []
    iptc_tags = [t for t in snap if t.startswith("IPTC:")]
    if iptc_tags and meta.is_jpeg:
        args += _iptc_prefix(meta, iptc_tags)
    for tag, value in snap.items():
        if isinstance(value, list) or tag in (SUBJECT, IPTC_KEYWORDS):
            args += _list_args(tag, _as_list(value))
        elif value is None:
            args.append(f"-{tag}=")
        elif tag in _DATE_TAGS:
            args.append(f"-{tag}#={value}")  # raw: a zero date "0000:00:00 ..." restores as-is
        else:
            args.append(f"-{tag}={value}")
    return WritePlan(args, tuple(snap))


def _values(args: Sequence[str]) -> list[str]:
    """The text values in "-TAG=value" style arguments (for redaction)."""
    out = []
    for a in args:
        if a.startswith("-") and "=" in a:
            out.append(a.split("=", 1)[1])
    return out


def redact(text: str, secrets: Iterable[str]) -> str:
    """Remove caption/tag text from ExifTool messages before they are logged."""
    for s in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(s, "<redacted>")
    return re.sub(r"\s+", " ", text).strip()
