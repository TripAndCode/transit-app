"""Pure helpers of pipeline.ch_aggregates."""

from datetime import date, datetime, timezone

import pytest

from pipeline import ch_aggregates


def test_months_of_names_each_month_once_in_order():
    days = [date(2027, 1, 3), date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)]
    assert ch_aggregates.months_of(days) == [202612, 202701]


@pytest.mark.parametrize(
    ("month", "bounds"),
    [
        (202609, (date(2026, 9, 1), date(2026, 10, 1))),
        (202612, (date(2026, 12, 1), date(2027, 1, 1))),
        (202802, (date(2028, 2, 1), date(2028, 3, 1))),
    ],
)
def test_month_bounds_run_from_the_first_to_the_next_months_first(month, bounds):
    assert ch_aggregates.month_bounds(month) == bounds


def test_month_where_bounds_the_month_at_the_service_day_cutover():
    """Service day 2026-09-01 starts at 04:00 JST, which is 19:00 UTC the day before."""
    sql, params = ch_aggregates.month_where(202609)
    assert "u.captured_at >= {month_start:DateTime64}" in sql
    assert "u.captured_at < {month_end:DateTime64}" in sql
    assert params == {
        "month_start": datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        "month_end": datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc),
    }
