from datetime import date

import pytest

from yta.parsing import (
    ValueParseError,
    parse_duration,
    parse_hours_to_minutes,
    parse_iso_date,
    parse_lenient,
    parse_number,
    parse_percent,
    parse_publish_date,
)


@pytest.mark.parametrize(
    ("parser", "raw", "expected"),
    [
        (parse_number, "", None),
        (parse_number, "0", 0.0),
        (parse_number, "1306.4214", 1306.4214),
        (parse_number, "-3", -3.0),
        (parse_percent, "3.54", pytest.approx(0.0354)),
        (parse_percent, "", None),
        (parse_duration, "0:04:04", 244.0),
        (parse_duration, "1:02:03", 3723.0),
        (parse_duration, "4:27", 267.0),
        (parse_duration, "", None),
        (parse_hours_to_minutes, "1.5", 90.0),
        (parse_publish_date, "Oct 2, 2025", date(2025, 10, 2)),
        (parse_publish_date, "Mar 16, 2026", date(2026, 3, 16)),
        (parse_publish_date, "2026-03-16", date(2026, 3, 16)),
        (parse_publish_date, "", None),
        (parse_iso_date, "2026-10-26", date(2026, 10, 26)),
        (parse_lenient, "0:01:00", 60.0),
        (parse_lenient, "n/a", None),
    ],
)
def test_parsers(parser, raw, expected):
    assert parser(raw) == expected


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_number, "abc"),
        (parse_number, "1,5"),
        (parse_number, "1.234.567"),
        (parse_number, "nan"),
        (parse_number, "inf"),
        (parse_percent, "3.5 %"),
        (parse_duration, "4:99"),
        (parse_duration, "abc"),
        (parse_publish_date, "Okt 2, 2025"),
        (parse_publish_date, "Feb 30, 2025"),
        (parse_iso_date, "26.10.2026"),
    ],
)
def test_malformed_values_raise(parser, raw):
    with pytest.raises(ValueParseError):
        parser(raw)


def test_error_names_location():
    err = ValueParseError("not a number: 'abc'", file="a.csv", row=7, column="Aufrufe")
    assert "a.csv" in str(err) and "row 7" in str(err) and "Aufrufe" in str(err)
