from datetime import UTC, date, datetime

import jdatetime

from app.processing.date_parsing import TEHRAN_TIMEZONE, parse_date_value


def jalali_to_gregorian(value: str) -> date:
    normalized = value.strip().replace("-", "/")
    year, month, day = (int(part) for part in normalized.split("/"))
    return jdatetime.date(year, month, day).togregorian()


def gregorian_to_jalali(value: date | datetime) -> str:
    gregorian_date = value.date() if isinstance(value, datetime) else value
    converted = jdatetime.date.fromgregorian(date=gregorian_date)
    return f"{converted.year:04d}/{converted.month:02d}/{converted.day:02d}"


def parse_published_at(value: str | None) -> datetime | None:
    return parse_date_value(value)


def publication_local_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(TEHRAN_TIMEZONE).date()
