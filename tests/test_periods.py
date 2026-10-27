from datetime import date

import pytest

from yta.periods import PeriodError, buckets, previous_period, previous_year, resolve

FIRST, LAST = date(2025, 5, 23), date(2026, 10, 26)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("all", (FIRST, LAST)),
        ("last_7_days", (date(2026, 10, 20), LAST)),
        ("last_28_days", (date(2026, 9, 29), LAST)),
        ("LAST_90_DAYS", (date(2026, 7, 29), LAST)),
        ("2026", (date(2026, 1, 1), date(2026, 12, 31))),
        ("2026-02", (date(2026, 2, 1), date(2026, 2, 28))),
        ("2026-09-01..2026-09-30", (date(2026, 9, 1), date(2026, 9, 30))),
    ],
)
def test_resolve(expr, expected):
    assert resolve(expr, FIRST, LAST) == expected


@pytest.mark.parametrize(
    "expr",
    ["yesterday", "last_0_days", "2026-13", "2026-09-30..2026-09-01", "2026-02-30..2026-03-01"],
)
def test_invalid_periods(expr):
    with pytest.raises(PeriodError):
        resolve(expr, FIRST, LAST)


def test_previous_period_and_year():
    assert previous_period(date(2026, 9, 29), LAST) == (date(2026, 9, 1), date(2026, 9, 28))
    assert previous_year(date(2024, 2, 29), date(2024, 3, 31)) == (
        date(2023, 2, 28),
        date(2023, 3, 31),
    )


def test_buckets():
    assert buckets(date(2026, 1, 30), date(2026, 3, 2), "month") == [
        (date(2026, 1, 30), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 2)),
    ]
    weeks = buckets(date(2026, 10, 21), date(2026, 10, 27), "week")  # Wed..Tue
    assert weeks == [
        (date(2026, 10, 21), date(2026, 10, 25)),
        (date(2026, 10, 26), date(2026, 10, 27)),
    ]
    assert len(buckets(date(2026, 1, 1), date(2026, 1, 3), "day")) == 3
    with pytest.raises(PeriodError):
        buckets(FIRST, LAST, "year")
