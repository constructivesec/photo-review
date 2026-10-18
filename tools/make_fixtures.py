"""Generate a folder of sample photos covering the fixture list in spec section 16.

Usage: python tools/make_fixtures.py OUTPUT_DIR [--size 1200x900] [--real-samples DIR]

Images are synthetic (a coloured card with the file name drawn on it, so each one is
easy to recognise in other apps). Metadata is written with the bundled ExifTool.
Files from --real-samples (for example an actual iPhone HEIC) are copied in as-is.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pillow_heif  # noqa: E402

from photo_review.metadata import ExifToolSession  # noqa: E402

pillow_heif.register_heif_opener()
FONT = ROOT / "photo_review" / "resources" / "fonts" / "DejaVuSans.ttf"

CAMERA = [
    "-IFD0:Make=Canon",
    "-IFD0:Model=Canon EOS 40D",
    "-ExifIFD:ExposureTime=1/125",
    "-ExifIFD:FNumber=5.6",
    "-ExifIFD:ISO=200",
    "-ExifIFD:FocalLength=50",
]


def card(label: str, size: tuple[int, int], color: tuple[int, int, int], mode: str = "RGB") -> Image.Image:
    im = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT), max(16, size[1] // 14))
    draw.rectangle(
        (size[0] // 10, size[1] // 10, size[0] * 9 // 10, size[1] * 9 // 10),
        outline=(255, 255, 255),
        width=max(2, size[1] // 150),
    )
    draw.text((size[0] / 2, size[1] / 2), label, font=font, fill=(255, 255, 255), anchor="mm")
    return im.convert(mode) if mode != "RGB" else im


def save(im: Image.Image, path: Path, fmt: str | None = None, **kw: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, fmt, **kw)
    return path


def make(out: Path, size: tuple[int, int], real_samples: Path | None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    w, h = size

    def jpg(name: str, color: tuple[int, int, int], meta: list[str] | None = None, **kw: object) -> Path:
        p = save(card(name, size, color), out / name, "JPEG", quality=92, **kw)
        if meta:
            et.write(p, meta)
        made.append(p)
        return p

    with ExifToolSession() as et:
        # iPhone-style HEIC (synthetic stand-in; add a real one with --real-samples)
        p = save(card("iphone.heic", (h, w), (40, 110, 160)), out / "iphone.heic", "HEIF", quality=80)
        et.write(
            p,
            [
                "-IFD0:Make=Apple",
                "-IFD0:Model=iPhone 15 Pro",
                "-IFD0:Software=17.5",
                "-ExifIFD:DateTimeOriginal=2024:07:04 18:30:12",
                "-ExifIFD:OffsetTimeOriginal=-05:00",
                "-ExifIFD:CreateDate=2024:07:04 18:30:12",
                "-ExifIFD:ExposureTime=1/250",
                "-ExifIFD:FNumber=1.8",
                "-ExifIFD:ISO=64",
                "-ExifIFD:FocalLength=6.9",
                "-GPS:GPSLatitude=40.7128",
                "-GPS:GPSLatitudeRef=N",
                "-GPS:GPSLongitude=74.0060",
                "-GPS:GPSLongitudeRef=W",
            ],
        )
        made.append(p)

        jpg(
            "lightroom.jpg",
            (150, 90, 40),
            [
                *CAMERA,
                "-ExifIFD:DateTimeOriginal=2015:12:25 09:15:00",
                "-XMP-xmp:Rating=3",
                "-XMP-xmp:CreatorTool=Adobe Photoshop Lightroom Classic 13.0",
                "-XMP-dc:Subject=family",
                "-XMP-dc:Subject=Christmas",
                "-XMP-dc:Subject=Lightroom Keyword",
                "-XMP-lr:HierarchicalSubject=People|Family",
                "-XMP-dc:Description=Christmas morning",
                "-XMP-dc:Title=Lightroom title",
                "-XMP-exif:DateTimeOriginal=2015:12:25 09:15:00",
                "-XMP-photoshop:DateCreated=2015:12:25 09:15:00",
            ],
        )
        jpg(
            "exif_no_xmp.jpg",
            (60, 130, 60),
            [
                *CAMERA,
                "-ExifIFD:DateTimeOriginal=2008:05:17 14:02:33",
                "-ExifIFD:CreateDate=2008:05:17 14:02:33",
            ],
        )
        made.append(save(card("no_metadata.png", size, (120, 60, 140)), out / "no_metadata.png", "PNG"))

        p = save(card("scan.tif", size, (90, 90, 90)), out / "scan.tif", "TIFF", compression="tiff_lzw")
        et.write(
            p,
            [
                "-IFD0:Make=EPSON",
                "-IFD0:Model=Perfection V600",
                "-ExifIFD:DateTimeOriginal=2024:03:10 10:00:00",
            ],
        )
        made.append(p)
        pages = [
            card(f"multipage.tif page {i + 1}", size, c)
            for i, c in enumerate([(200, 60, 60), (60, 200, 60), (60, 60, 200)])
        ]
        p = out / "multipage.tif"
        pages[0].save(p, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_lzw")
        made.append(p)

        jpg("zero_date.jpg", (100, 100, 30), ["-ExifIFD:DateTimeOriginal#=0000:00:00 00:00:00"])
        jpg(
            "mismatched_dates.jpg",
            (30, 100, 100),
            [
                "-ExifIFD:DateTimeOriginal=1975:07:04 12:00:00",
                "-XMP-exif:DateTimeOriginal=1976:08:05 12:00:00",
            ],
        )
        jpg(
            "scanner_epson.jpg",
            (110, 80, 60),
            [
                "-IFD0:Make=EPSON",
                "-IFD0:Model=Perfection V600",
                "-IFD0:Software=EPSON Scan",
                "-ExifIFD:DateTimeOriginal=2024:03:10 10:21:44",
                "-ExifIFD:CreateDate=2024:03:10 10:21:44",
            ],
        )
        jpg("no_date.jpg", (70, 70, 120))
        # Older software: IPTC in Latin-1 with no CodedCharacterSet declared
        p = jpg("legacy_iptc_latin1.jpg", (120, 100, 70))
        et.execute(
            "-overwrite_original",
            "-P",
            "-charset",
            "iptc=Latin",
            "-IPTC:By-line=Müller",
            "-IPTC:City=Zürich",
            "-IPTC:Caption-Abstract=Café",
            str(p),
        )
        jpg(
            "june_1962.jpg",
            (140, 120, 80),
            [
                "-ExifIFD:DateTimeOriginal=1962:06:15 12:00:00",
                "-XMP-exif:DateTimeOriginal=1962:06:15 12:00:00",
                "-XMP-dc:Subject=date:approximate",
            ],
        )
        jpg(
            "december_1899.jpg",
            (90, 70, 50),
            [
                "-ExifIFD:DateTimeOriginal=1899:12:01 12:00:00",
                "-XMP-exif:DateTimeOriginal=1899:12:01 12:00:00",
            ],
        )
        jpg("Grand-mère à la plage 1962.v2.final.jpg", (40, 140, 140))

        good = card("corrupt.jpg", size, (200, 0, 0))
        p = save(good, out / "corrupt.jpg", "JPEG")
        p.write_bytes(p.read_bytes()[: p.stat().st_size // 3])  # truncated
        made.append(p)

        p = jpg("readonly.jpg", (80, 80, 80))
        os.chmod(p, 0o444)

        # AppleDouble companion: must be ignored by the scanner
        (out / "._readonly.jpg").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00" + b"\x00" * 74)
        made.append(out / "._readonly.jpg")

        # Display robustness
        made.append(save(Image.new("I;16", size, 30000), out / "sixteen_bit.png", "PNG"))
        made.append(
            save(
                card("palette.png", size, (10, 120, 200)).convert("P", palette=Image.ADAPTIVE),
                out / "palette.png",
                "PNG",
            )
        )
        rgba = card("alpha.png", size, (200, 120, 10)).convert("RGBA")
        rgba.putalpha(160)
        made.append(save(rgba, out / "alpha.png", "PNG"))
        made.append(save(card("cmyk.jpg", size, (10, 160, 90), "CMYK"), out / "cmyk.jpg", "JPEG"))
        frames = [
            card(f"animated.png frame {i + 1}", size, c) for i, c in enumerate([(250, 200, 0), (0, 200, 250)])
        ]
        p = out / "animated.png"
        frames[0].save(p, "PNG", save_all=True, append_images=frames[1:], duration=500, loop=0)
        made.append(p)
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        jpg("with_icc_profile.jpg", (180, 40, 120), icc_profile=srgb)
        # Stored sideways with EXIF Orientation 6 (display = rotate 90 CW)
        jpg("rotated_exif6.jpg", (30, 60, 160), ["-IFD0:Orientation#=6", "-XMP-dc:Description=Rotated"])

        # Ignored files
        (out / "Thumbs.db").write_bytes(b"\x00" * 64)
        (out / "empty.jpg").write_bytes(b"")
        (out / "IMG_0001.mov").write_bytes(b"\x00\x00\x00\x14ftypqt  ")
        (out / ".hidden.jpg").write_bytes((out / "no_date.jpg").read_bytes())
        try:
            (out / "symlink.jpg").symlink_to(out / "no_date.jpg")
        except OSError:
            pass  # Windows without symlink privilege

        # Already-archived photo
        p = save(card("archived.jpg", size, (60, 60, 60)), out / "_archive" / "archived.jpg", "JPEG")
        et.write(p, ["-XMP-xmp:Rating=-1", "-XMP-dc:Subject=status:archive"])
        made.append(p)

    if real_samples:
        for f in sorted(real_samples.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                made.append(Path(shutil.copy2(f, out / f"real_{f.name}")))
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", default="1200x900", help="WIDTHxHEIGHT of generated images")
    parser.add_argument("--real-samples", type=Path, help="folder of real photos to copy in")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"{args.output} is not empty")
    w, h = (int(v) for v in args.size.lower().split("x"))
    made = make(args.output, (w, h), args.real_samples)
    print(f"Created {len(made)} sample files in {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
