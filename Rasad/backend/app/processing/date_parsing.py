"""Framework-free parsing of Gregorian, Jalali and feed date values."""

import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import jdatetime

from app.processing.text import normalize_text


TEHRAN_TIMEZONE = ZoneInfo("Asia/Tehran")
DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
PERSIAN_MONTHS = {
    "فروردین": 1,
    "اردیبهشت": 2,
    "خرداد": 3,
    "تیر": 4,
    "مرداد": 5,
    "شهریور": 6,
    "مهر": 7,
    "آبان": 8,
    "آذر": 9,
    "دی": 10,
    "بهمن": 11,
    "اسفند": 12,
}


def normalize_date_text(value: str) -> str:
    return normalize_text(value.translate(DIGIT_TRANSLATION)).strip(" \t\r\n,؛;|–—")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=TEHRAN_TIMEZONE)
    return value.astimezone(UTC)


def _numeric_date(value: str) -> datetime | None:
    match = re.search(
        r"(?<!\d)(?P<year>1[34]\d{2}|(?:19|20)\d{2})\s*[/.-]\s*"
        r"(?P<month>0?[1-9]|1[0-2])\s*[/.-]\s*(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)",
        value,
    )
    if not match:
        match = re.search(
            r"(?<!\d)(?P<year>1[34]\d{2}|(?:19|20)\d{2})"
            r"(?P<month>0[1-9]|1[0-2])(?P<day>0[1-9]|[12]\d|3[01])(?!\d)",
            value,
        )
    if not match:
        return None
    year, month, day = (int(match.group(part)) for part in ("year", "month", "day"))
    try:
        parsed_date = (
            jdatetime.date(year, month, day).togregorian()
            if 1300 <= year <= 1499
            else date(year, month, day)
        )
    except ValueError:
        return None
    return _utc(datetime.combine(parsed_date, datetime.min.time()))


def _persian_named_date(value: str) -> datetime | None:
    months = "|".join(PERSIAN_MONTHS)
    match = re.search(
        rf"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])\s+"
        rf"(?P<month>{months})\s+(?P<year>1[34]\d{{2}})(?!\d)",
        value,
    )
    if not match:
        return None
    try:
        parsed = jdatetime.date(
            int(match.group("year")),
            PERSIAN_MONTHS[match.group("month")],
            int(match.group("day")),
        ).togregorian()
    except ValueError:
        return None
    return _utc(datetime.combine(parsed, datetime.min.time()))


def parse_date_value(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = normalize_date_text(value)
    try:
        return _utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _utc(parsedate_to_datetime(normalized))
    except (TypeError, ValueError):
        pass
    parsed = _numeric_date(normalized) or _persian_named_date(normalized)
    if parsed is not None:
        return parsed
    for pattern in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return _utc(datetime.strptime(normalized.replace(",", ""), pattern))
        except ValueError:
            continue
    return None
