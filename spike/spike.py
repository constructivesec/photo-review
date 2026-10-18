"""Metadata spike (spec section 17, step 1).

Writes Rating, Description (with mirrors), Subject and DateTimeOriginal (including
pre-1970 dates) into JPEG, PNG, TIFF and HEIC samples with the bundled ExifTool,
re-reads them, and checks that:
  * every written value reads back exactly,
  * decoded pixel data (all frames/pages) is identical before and after,
  * no non-edited tag changed or disappeared (ExifTool dumps compared),
  * the file modification time is preserved (-P).
It also builds a set of files for the manual compatibility checks (step 2).

Usage: python spike/spike.py [--samples DIR] [--out spike/out]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_fixtures  # noqa: E402
import pillow_heif  # noqa: E402
from PIL import Image, ImageSequence  # noqa: E402

from photo_review.metadata import ExifToolSession, MetadataError  # noqa: E402

pillow_heif.register_heif_opener()

CAPTION = "Picnic at the lake house. Grand-mère, Émile & 日本の友達"
TAGS = ["person:emma", "event:christmas-2025", "place:lake-house", "date:approximate"]
TRICKY = [
    "-starts with a dash",
    "@starts with an at sign",
    "<starts with angle> & $HOME",
    "has \"double\" and 'single' quotes",
    "line one\nline two\r\nline three",
    "emoji 😀👨‍👩‍👧 é ñ ü 日本語",
    "back\\slash and literal \\n",
    "  leading and trailing spaces  ",
    "#starts with hash",
]
DATE_1962 = "1962:06:15 12:00:00"
DATE_1899 = "1899:12:01 12:00:00"

# Keys that legitimately change or are derived on every write.
VOLATILE_PREFIXES = ("SourceFile", "ExifTool:", "System:", "Composite:")
VOLATILE_KEYS = {"File:FileSize", "File:CurrentIPTCDigest", "Photoshop:IPTCDigest"}
# Structural pointers/sizes that move when metadata grows. Image data they point to is
# verified separately (decoded pixels; embedded thumbnails/previews compared byte-for-byte).
STRUCTURAL = re.compile(r"(Offset|Offsets|ByteCounts|^QuickTime:MediaData(Size|Offset)?)$")
EMBEDDED_IMAGES = re.compile(r":(ThumbnailImage|PreviewImage|JpgFromRaw|OtherImage|ThumbnailTIFF)$")
# Housekeeping ExifTool always updates when it rewrites XMP.
HOUSEKEEPING = {"XMP-x:XMPToolkit"}


def kind(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".png": "png",
        ".tif": "tiff",
        ".tiff": "tiff",
        ".heic": "heic",
        ".heif": "heic",
    }[ext]


# ---------------------------------------------------------------- field writers (spec 6-9)


def caption_args(fmt: str, text: str, mirror: bool = True) -> tuple[list[str], set[str]]:
    args = [f"-XMP-dc:Description={text}"]
    keys = {"XMP-dc:Description"}
    if mirror:
        args.append(f"-IFD0:ImageDescription={text}")
        keys.add("IFD0:ImageDescription")
        if fmt == "jpeg":
            iptc = text.encode("utf-8")[:2000].decode("utf-8", errors="ignore")
            args += [
                f"-IPTC:Caption-Abstract={iptc}",
                "-IPTC:CodedCharacterSet=UTF8",
                f"-IFD0:XPComment={text}",
            ]
            keys |= {
                "IPTC:Caption-Abstract",
                "IPTC:CodedCharacterSet",
                "IFD0:XPComment",
                "IPTC:ApplicationRecordVersion",
            }
    return args, keys


def subject_args(fmt: str, tags: list[str]) -> tuple[list[str], set[str]]:
    targets = ["XMP-dc:Subject"] + (["IPTC:Keywords"] if fmt == "jpeg" else [])
    args: list[str] = []
    for t in targets:
        args += [f"-{t}={v}" for v in tags] if tags else [f"-{t}="]
    keys = set(targets) | (
        {"IPTC:CodedCharacterSet", "IPTC:ApplicationRecordVersion"} if fmt == "jpeg" else set()
    )
    if fmt == "jpeg":
        args.append("-IPTC:CodedCharacterSet=UTF8")
    return args, keys


def date_args(value: str) -> tuple[list[str], set[str]]:
    tags = ["ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal", "XMP-photoshop:DateCreated"]
    return [f"-{t}={value}" for t in tags], set(tags)


def norm_date(v: object) -> str:
    s = str(v)
    return (s[:10].replace("-", ":") + " " + s[11:19]) if len(s) >= 19 else s


# ---------------------------------------------------------------- checks


def pixel_digest(path: Path) -> list[tuple[str, tuple[int, int], str]]:
    out = []
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            fr = frame.copy()
            out.append((fr.mode, fr.size, hashlib.sha256(fr.tobytes()).hexdigest()))
    return out


def dump(et: ExifToolSession, path: Path) -> dict:
    data = et.read_json([path], extra=["-a", "-u"])[0]
    return {k: v for k, v in data.items() if not k.startswith(VOLATILE_PREFIXES) and k not in VOLATILE_KEYS}


def embedded_images(et: ExifToolSession, path: Path, before: dict) -> dict[str, str]:
    return {
        k: hashlib.sha256(et.execute_bytes("-b", f"-{k}", str(path))).hexdigest()
        for k in before
        if EMBEDDED_IMAGES.search(k)
    }


@dataclass
class Result:
    sample: str
    case: str
    ok: bool = True
    notes: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    housekeeping: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.notes.append(msg)


Steps = Callable[[Path, str, dict], tuple[list[list[str]], set[str], dict[str, object]]]


def run_case(et: ExifToolSession, src: Path, work: Path, case: str, steps: Steps) -> Result:
    res = Result(src.name, case)
    p = work / f"{case}__{src.name}"
    shutil.copy2(src, p)
    os.utime(p, (1_000_000_000, 1_000_000_000))
    before_pixels, before = pixel_digest(p), dump(et, p)
    before_embedded = embedded_images(et, p, before)
    calls, edited, expected = steps(p, kind(p), before)
    try:
        for args in calls:
            et.write(p, args)
    except MetadataError as exc:
        res.fail(f"write failed: {exc}")
        return res
    if et.last_warnings:
        res.notes.append(f"ExifTool: {et.last_warnings}")
    after = dump(et, p)

    for key, want in expected.items():
        got = after.get(key)
        if key.endswith(("DateTimeOriginal", "DateCreated")) and got is not None:
            got = norm_date(got)
        if isinstance(want, list) and isinstance(got, str):
            got = [got]
        if want is None:
            if got is not None:
                res.fail(f"{key} should be absent, is {got!r}")
        elif got != want:
            res.fail(f"{key}: expected {want!r}, got {got!r}")

    if pixel_digest(p) != before_pixels:
        res.fail("decoded pixel data changed")
    if embedded_images(et, p, before) != before_embedded:
        res.fail("embedded thumbnail/preview image data changed")
    for key, val in before.items():
        if key in edited or STRUCTURAL.search(key) or EMBEDDED_IMAGES.search(key):
            continue
        if key in HOUSEKEEPING:
            if after.get(key) != val:
                res.housekeeping.append(f"{key}: {val!r} -> {after.get(key)!r}")
            continue
        if key not in after:
            res.fail(f"non-edited tag removed: {key}")
        elif after[key] != val:
            res.fail(f"non-edited tag changed: {key}: {val!r} -> {after[key]!r}")
    res.added = sorted(k for k in after if k not in before and k not in edited)
    if int(p.stat().st_mtime) != 1_000_000_000:
        res.fail("file modification time not preserved")
    return res


def case_full_write(p: Path, fmt: str, before: dict, date: str = DATE_1962):
    c, ck = caption_args(fmt, CAPTION)
    s, sk = subject_args(fmt, TAGS)
    d, dk = date_args(date)
    expected: dict[str, object] = {
        "XMP-xmp:Rating": 1,
        "XMP-dc:Description": CAPTION,
        "IFD0:ImageDescription": CAPTION,
        "XMP-dc:Subject": TAGS,
        **{k: date for k in dk},
    }
    if fmt == "jpeg":
        expected |= {"IPTC:Caption-Abstract": CAPTION, "IFD0:XPComment": CAPTION, "IPTC:Keywords": TAGS}
    return [["-XMP-xmp:Rating=1", *c, *s, *d]], {"XMP-xmp:Rating"} | ck | sk | dk, expected


def case_1899(p: Path, fmt: str, before: dict):
    return case_full_write(p, fmt, before, DATE_1899)


def existing_subject(before: dict) -> list[str]:
    v = before.get("XMP-dc:Subject", [])
    return [v] if isinstance(v, str) else list(v)


def case_archive_then_keep(p: Path, fmt: str, before: dict):
    """ARCHIVE (Rating -1 + status:archive), then KEEP (Rating 1, remove status:archive)."""
    archive = ["-XMP-xmp:Rating=-1", "-XMP-dc:Subject-=status:archive", "-XMP-dc:Subject+=status:archive"]
    keep = ["-XMP-xmp:Rating=1", "-XMP-dc:Subject-=status:archive"]
    other = [t for t in existing_subject(before) if t != "status:archive"]
    return (
        [archive, keep],
        {"XMP-xmp:Rating", "XMP-dc:Subject"},
        {"XMP-xmp:Rating": 1, "XMP-dc:Subject": other or None},
    )


def case_archive(p: Path, fmt: str, before: dict):
    archive = ["-XMP-xmp:Rating=-1", "-XMP-dc:Subject-=status:archive", "-XMP-dc:Subject+=status:archive"]
    # Run twice: the keyword must not be duplicated.
    return (
        [archive, archive],
        {"XMP-xmp:Rating", "XMP-dc:Subject"},
        {"XMP-xmp:Rating": -1, "XMP-dc:Subject": existing_subject(before) + ["status:archive"]},
    )


def make_tricky_case(text: str):
    def steps(p: Path, fmt: str, before: dict):
        c, ck = caption_args(fmt, text)
        s, sk = subject_args(fmt, [text, "second"])
        expected: dict[str, object] = {
            "XMP-dc:Description": text,
            "IFD0:ImageDescription": text,
            "XMP-dc:Subject": [text, "second"],
        }
        if fmt == "jpeg":
            expected |= {
                "IPTC:Caption-Abstract": text,
                "IFD0:XPComment": text,
                "IPTC:Keywords": [text, "second"],
            }
        return [[*c, *s]], ck | sk, expected

    return steps


def case_clear(p: Path, fmt: str, before: dict):
    c1, ck = caption_args(fmt, CAPTION)
    c2, _ = caption_args(fmt, "")
    expected = {k: None for k in ck if k not in {"IPTC:CodedCharacterSet", "IPTC:ApplicationRecordVersion"}}
    return [c1, c2], ck, expected


def case_long_caption(p: Path, fmt: str, before: dict):
    text = "Long caption é. " * 200  # 3400 UTF-8 bytes
    c, ck = caption_args(fmt, text)
    expected: dict[str, object] = {"XMP-dc:Description": text}
    if fmt == "jpeg":
        expected["IPTC:Caption-Abstract"] = text.encode()[:2000].decode(errors="ignore")
    return [c], ck, expected


CASES: dict[str, Callable] = {
    "full_write_1962": case_full_write,
    "date_1899": case_1899,
    "archive_twice_no_duplicate": case_archive,
    "archive_then_keep": case_archive_then_keep,
    "clear_caption": case_clear,
    "long_caption_iptc_truncated": case_long_caption,
    **{f"tricky_{i}": make_tricky_case(t) for i, t in enumerate(TRICKY)},
}


# ---------------------------------------------------------------- compatibility set (step 2)


def build_compat_set(et: ExifToolSession, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    size = (1200, 900)
    specs = [  # (suffix, colour, rating, caption, tags, date)
        (
            "A_dated_2005",
            (40, 110, 160),
            1,
            "Dated August 2005, no approximate flag",
            ["person:emma", "place:lake-house"],
            "2005:08:20 12:00:00",
        ),
        ("B_captioned_1962", (150, 90, 40), 1, CAPTION, TAGS, DATE_1962),
        ("C_undated", (60, 130, 60), 1, "This photo has no date", ["event:unknown"], None),
        ("D_archived", (90, 90, 90), -1, "Archived photo", ["status:archive"], "1990:01:01 12:00:00"),
    ]
    formats = [
        ("jpg", "JPEG", "jpeg"),
        ("heic", "HEIF", "heic"),
        ("png", "PNG", "png"),
        ("tif", "TIFF", "tiff"),
    ]
    for ext, pil_fmt, fmt in formats:
        for suffix, color, rating, caption, tags, date in specs:
            name = f"{ext}_{suffix}.{ext}"
            p = make_fixtures.save(make_fixtures.card(name, size, color), out / name, pil_fmt)
            c, _ = caption_args(fmt, caption)
            s, _ = subject_args(fmt, tags)
            d = date_args(date)[0] if date else []
            et.write(p, [f"-XMP-xmp:Rating={rating}", *c, *s, *d])
    p = make_fixtures.save(
        make_fixtures.card("jpg_E_keep_with_exif_rating.jpg", size, (120, 60, 140)),
        out / "jpg_E_keep_with_exif_rating.jpg",
        "JPEG",
    )
    et.write(
        p,
        [
            "-XMP-xmp:Rating=1",
            "-IFD0:Rating=1",
            "-IFD0:RatingPercent=1",
            *caption_args("jpeg", "XMP rating plus EXIF Rating and RatingPercent")[0],
        ],
    )
    archive = out.parent / "compatibility_samples.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.iterdir()):
            zf.write(f, f"compatibility_samples/{f.name}")
    return archive


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=Path, help="extra real photos to test (JPEG/PNG/TIFF/HEIC)")
    parser.add_argument("--out", type=Path, default=ROOT / "spike" / "out")
    args = parser.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, ExifToolSession() as et:
        tmp_path = Path(tmp)
        gen = tmp_path / "generated"
        make_fixtures.make(gen, (800, 600), args.samples)
        names = [
            "exif_no_xmp.jpg",
            "lightroom.jpg",
            "no_metadata.png",
            "scan.tif",
            "multipage.tif",
            "legacy_iptc_latin1.jpg",
            "iphone.heic",
        ]
        samples = [gen / n for n in names]
        samples += sorted(
            p
            for p in gen.glob("real_*")
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif"}
        )
        work = tmp_path / "work"
        work.mkdir()

        results = [run_case(et, s, work, case, fn) for s in samples for case, fn in CASES.items()]
        version = et.version
        compat_zip = build_compat_set(et, args.out / "compatibility_samples")

    lines = [
        f"# Spike results (ExifTool {version})",
        "",
        "| Sample | Cases passed | Failures |",
        "|---|---|---|",
    ]
    for s in samples:
        rs = [r for r in results if r.sample == s.name]
        bad = [r for r in rs if not r.ok]
        lines.append(
            f"| {s.name} | {len(rs) - len(bad)}/{len(rs)} | "
            + ("; ".join(f"{r.case}: {' / '.join(r.notes)}" for r in bad) or "none")
            + " |"
        )
    lines += ["", "## Tags ExifTool added beyond the edited ones (informational)", ""]
    for s in samples:
        added = sorted({a for r in results if r.sample == s.name for a in r.added})
        lines.append(f"- {s.name}: {', '.join(added) or 'none'}")
    lines += ["", "## Housekeeping values ExifTool changed (informational)", ""]
    for s in samples:
        hk = sorted({h for r in results if r.sample == s.name for h in r.housekeeping})
        lines.append(f"- {s.name}: {', '.join(hk) or 'none'}")
    notes = sorted({f"{r.sample} / {r.case}: {n}" for r in results if r.ok for n in r.notes})
    if notes:
        lines += ["", "## Warnings on passing cases", "", *(f"- {n}" for n in notes)]
    lines += ["", f"Compatibility samples for manual checks: {compat_zip.relative_to(ROOT)}"]
    report = "\n".join(lines) + "\n"
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    failed = sum(not r.ok for r in results)
    print(f"{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
