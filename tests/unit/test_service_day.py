from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pipeline.service_day import (
    DAY_TYPES,
    SERVICE_DAY_CUTOVER_HOURS,
    day_type,
    public_holidays_between,
    service_date_ch,
    service_date_of,
)


def _jst(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # An after-midnight stop belongs to the service day still running.
        (_jst(2026, 3, 7, 0, 15), date(2026, 3, 6)),
        (_jst(2026, 3, 7, 3, 59), date(2026, 3, 6)),
        # The cutover itself opens the next service day.
        (_jst(2026, 3, 7, SERVICE_DAY_CUTOVER_HOURS, 0), date(2026, 3, 7)),
        (_jst(2026, 3, 7, 12, 0), date(2026, 3, 7)),
        (_jst(2026, 3, 7, 23, 50), date(2026, 3, 7)),
    ],
)
def test_service_date_of_moves_the_day_boundary_to_the_cutover(moment, expected):
    assert service_date_of(moment) == expected


def test_service_date_of_reads_a_utc_instant_in_japan_time():
    # 2026-03-06 15:30 UTC is 2026-03-07 00:30 JST: still service day the 6th.
    assert service_date_of(datetime(2026, 3, 6, 15, 30, tzinfo=timezone.utc)) == date(2026, 3, 6)


def test_the_sql_fragment_names_the_column_it_is_given():
    assert "u.captured_at" in service_date_ch("u.captured_at")
    assert service_date_ch().count("captured_at") == 1


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 16), "weekday"),  # an ordinary Wednesday
        (date(2026, 9, 19), "saturday"),
        (date(2026, 9, 20), "sunday_holiday"),
        # Public holidays on a weekday run the holiday timetable.
        (date(2026, 9, 21), "sunday_holiday"),  # Respect for the Aged Day, a Monday
        (date(2026, 9, 22), "sunday_holiday"),  # citizens' holiday between two holidays
        (date(2026, 5, 6), "sunday_holiday"),  # substitute holiday for Sunday 5/3
        # A public holiday on a Saturday is a holiday; the year-end break is not one.
        (date(2025, 5, 3), "sunday_holiday"),  # Constitution Memorial Day, a Saturday
        (date(2026, 1, 3), "saturday"),
    ],
)
def test_day_type_reads_the_weekday_and_the_public_holiday_calendar(day, expected):
    assert day_type(day) == expected


def test_day_type_matches_the_holiday_list_it_is_given():
    holidays = frozenset(public_holidays_between(date(2026, 9, 14), date(2026, 9, 27)))
    assert holidays == {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)}
    for offset in range(14):
        d = date(2026, 9, 14) + timedelta(days=offset)
        assert day_type(d, holidays) == day_type(d)


def test_day_types_are_listed_in_display_order():
    assert DAY_TYPES == ("weekday", "saturday", "sunday_holiday")
