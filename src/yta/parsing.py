"""Value parsers for YouTube Studio CSV cells (German and English UI)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DURATION_RE = re.compile(r"^(\d+):([0-5]\d)(?::([0-5]\d))?$")
_PUBLISH_DATE_RE = re.compile(r"^([A-Za-z]{3}) (\d{1,2}), (\d{4})$")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

Parser = Callable[[str], float | None]


class ValueParseError(ValueError):
    """A cell could not be parsed; carries enough context to locate it."""

    def __init__(self, message: str, *, file: str = "", row: int = 0, column: str = "") -> None:
        self.file = file
        self.row = row
        self.column = column
        location = f"{file}, row {row}, column '{column}': " if file else ""
        super().__init__(f"{location}{message}")


def _clean(value: str) -> str:
    return value.strip()


def parse_number(value: str) -> float | None:
    """Plain decimal number with a decimal point and no thousands separator."""
    value = _clean(value)
    if value == "":
        return None
    if not _NUMBER_RE.match(value):
        raise ValueParseError(f"not a number: {value!r}")
    return float(value)


def parse_percent(value: str) -> float | None:
    """Percentage as shown by Studio (``3.54``) to a fraction (``0.0354``)."""
    number = parse_number(value)
    # Studio exports at most 4 decimals; rounding removes binary float noise (4.1 / 100).
    return None if number is None else round(number / 100, 10)


def parse_duration(value: str) -> float | None:
    """``H:MM:SS`` or ``M:SS`` to seconds."""
    value = _clean(value)
    if value == "":
        return None
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueParseError(f"not a duration (H:MM:SS): {value!r}")
    first, second, third = match.groups()
    if third is None:
        return float(int(first) * 60 + int(second))
    return float(int(first) * 3600 + int(second) * 60 + int(third))


def parse_hours_to_minutes(value: str) -> float | None:
    """Watch time in hours to minutes."""
    number = parse_number(value)
    return None if number is None else number * 60


def parse_iso_date(value: str) -> date:
    value = _clean(value)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueParseError(f"not a date (YYYY-MM-DD): {value!r}") from None


def parse_publish_date(value: str) -> date | None:
    """Publish date as exported by Studio (``Oct 2, 2025``, also in the German UI) or ISO."""
    value = _clean(value)
    if value == "":
        return None
    match = _PUBLISH_DATE_RE.match(value)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month is not None:
            try:
                return date(int(match.group(3)), month, int(match.group(2)))
            except ValueError:
                pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueParseError(f"not a publish date: {value!r}") from None


def parse_lenient(value: str) -> float | None:
    """Best effort for columns without a known mapping: number or duration, else missing."""
    for parser in (parse_number, parse_duration):
        try:
            return parser(value)
        except ValueParseError:
            continue
    return None
