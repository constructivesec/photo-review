"""Date taken rules (spec section 9). Pure functions, no I/O."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

MIN_YEAR = 1826
NOON = (12, 0, 0)

# "YYYY:MM:DD[ HH:MM[:SS][.fff]][Z|+HH:MM]" as ExifTool displays EXIF and XMP dates.
_DATE_RE = re.compile(
    r"^(\d{4})[:-](\d{2})[:-](\d{2})"
    r"(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?"
    r"\s*(?:Z|[+-]\d{2}:?\d{2})?$"
)


def parse(value: str | None) -> datetime | None:
    """Parse a stored date. Blank, zero ("0000:00:00 00:00:00"), partial and malformed values
    return None. Timezone offsets are ignored (decision Q5); a missing time is 00:00:00."""
    if not value:
        return None
    m = _DATE_RE.match(str(value).strip())
    if not m:
        return None
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    hh, mm, ss = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:  # year 0, month 0/13, Feb 30, hour 24, ...
        return None


@dataclass(frozen=True)
class EffectiveDate:
    value: datetime | None
    mismatch: bool  # EXIF and XMP both usable and different

    @property
    def undated(self) -> bool:
        return self.value is None


def effective_date(exif_dto: str | None, xmp_dto: str | None) -> EffectiveDate:
    """EXIF DateTimeOriginal, else XMP-exif DateTimeOriginal. EXIF wins when both differ."""
    e, x = parse(exif_dto), parse(xmp_dto)
    return EffectiveDate(e or x, e is not None and x is not None and e != x)


def format_exif(dt: datetime) -> str:
    """The value written to all date fields: no timezone offset (decision Q5)."""
    return f"{dt.year:04d}:{dt.month:02d}:{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


def format_display(dt: datetime) -> str:
    """Stored date label, for example "1962-06-15 12:00:00"."""
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"


class DateInputError(ValueError):
    """Invalid month/year input. The message is shown in the status bar."""


def validate(month_text: str, year_text: str, today: date | None = None) -> tuple[int, int]:
    """Validate the Month and Year fields. Returns (month, year)."""
    today = today or date.today()
    m_txt, y_txt = month_text.strip(), year_text.strip()
    if not m_txt.isdigit() or not y_txt.isdigit():
        raise DateInputError("Enter the month as a number (1 to 12) and the year as four digits.")
    if len(y_txt) != 4:
        raise DateInputError("Enter the year as four digits, for example 1962.")
    month, year = int(m_txt), int(y_txt)
    if not 1 <= month <= 12:
        raise DateInputError("The month must be a number from 1 to 12.")
    if not MIN_YEAR <= year <= today.year:
        raise DateInputError(f"The year must be between {MIN_YEAR} and {today.year}.")
    if (year, month) > (today.year, today.month):
        raise DateInputError("That month is in the future.")
    return month, year


def new_value(previous: datetime | None, month: int, year: int) -> datetime:
    """Previously dated: keep the day (clamped to the month) and the time of day.
    Previously blank: day 1 at 12:00:00."""
    if previous is None:
        return datetime(year, month, 1, *NOON)
    last = calendar.monthrange(year, month)[1]
    return previous.replace(year=year, month=month, day=min(previous.day, last))


def csv_date(dt: datetime | None, approximate: bool) -> str:
    """captions.csv: YYYY-MM, or YYYY-MM-DD when the date is not approximate."""
    if dt is None:
        return ""
    return f"{dt.year:04d}-{dt.month:02d}" if approximate else f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
