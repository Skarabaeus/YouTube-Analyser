"""Resolve period expressions to concrete date ranges.

Relative periods are anchored at the last day with data, not at today, because Studio
exports lag behind.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

LAST_N_RE = re.compile(r"^last_(\d{1,4})_days$")
YEAR_RE = re.compile(r"^(\d{4})$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")

PERIOD_HELP = (
    "use 'last_7_days', 'last_28_days', 'last_90_days', 'last_365_days' (any last_N_days), "
    "'all', 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD..YYYY-MM-DD'"
)


class PeriodError(ValueError):
    pass


def resolve(expression: str, first_day: date, last_day: date) -> tuple[date, date]:
    """Turn a period expression into (start, end), both inclusive."""
    text = expression.strip().lower()
    if text == "all":
        return first_day, last_day
    if match := LAST_N_RE.match(text):
        days = int(match.group(1))
        if days < 1:
            raise PeriodError("last_N_days needs N >= 1")
        return last_day - timedelta(days=days - 1), last_day
    if match := YEAR_RE.match(text):
        year = int(match.group(1))
        return date(year, 1, 1), date(year, 12, 31)
    if match := MONTH_RE.match(text):
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise PeriodError(f"invalid month in '{expression}'")
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if match := RANGE_RE.match(text):
        try:
            start, end = date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
        except ValueError:
            raise PeriodError(f"invalid date in '{expression}'") from None
        if end < start:
            raise PeriodError("period end is before period start")
        return start, end
    raise PeriodError(f"unknown period '{expression}': {PERIOD_HELP}")


def previous_period(start: date, end: date) -> tuple[date, date]:
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def previous_year(start: date, end: date) -> tuple[date, date]:
    return _minus_year(start), _minus_year(end)


def _minus_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def buckets(start: date, end: date, granularity: str) -> list[tuple[date, date]]:
    """Split [start, end] into day, ISO-week or calendar-month buckets (clipped to the range)."""
    if granularity not in ("day", "week", "month"):
        raise PeriodError("granularity must be 'day', 'week' or 'month'")
    result: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if granularity == "day":
            bucket_end = cursor
        elif granularity == "week":
            bucket_end = cursor + timedelta(days=6 - cursor.weekday())
        else:
            bucket_end = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        bucket_end = min(bucket_end, end)
        result.append((cursor, bucket_end))
        cursor = bucket_end + timedelta(days=1)
    return result
