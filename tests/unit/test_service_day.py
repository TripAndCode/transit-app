from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pipeline.clickhouse import jst_midnight_utc
from pipeline.service_day import (
    SERVICE_DAY_CUTOVER_HOURS,
    service_date_ch,
    service_date_of,
    service_day_moment_utc,
    service_day_offset,
    service_day_seconds,
    service_day_seconds_sql,
    service_day_start_utc,
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


@pytest.mark.parametrize("day", [date(2026, 3, 6), date(2026, 3, 7), date(2026, 12, 31)])
def test_a_service_day_starts_at_the_cutover_hour_of_its_date(day):
    start = service_day_start_utc(day)
    assert start == jst_midnight_utc(day) + timedelta(hours=SERVICE_DAY_CUTOVER_HOURS)
    assert start.utcoffset() == timedelta(0)
    assert service_date_of(start) == day
    assert service_date_of(start - timedelta(microseconds=1)) == day - timedelta(days=1)
    assert service_date_of(service_day_start_utc(day + timedelta(days=1)) - timedelta(microseconds=1)) == day


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        # A raw value before the cutover is an after-midnight stop, a day later.
        (10 * 60, 24 * 3600 + 10 * 60),
        (SERVICE_DAY_CUTOVER_HOURS * 3600 - 1, 24 * 3600 + SERVICE_DAY_CUTOVER_HOURS * 3600 - 1),
        # The cutover itself and a GTFS extended hour already sit in the service day.
        (SERVICE_DAY_CUTOVER_HOURS * 3600, SERVICE_DAY_CUTOVER_HOURS * 3600),
        (24 * 3600 + 10 * 60, 24 * 3600 + 10 * 60),
    ],
)
def test_service_day_offset_places_a_raw_value_as_service_day_seconds_does_a_clock(seconds, expected):
    assert service_day_offset(seconds) == expected
    h, rem = divmod(seconds, 3600)
    assert service_day_seconds(h, rem // 60, rem % 60) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (6 * 3600 + 5 * 60, _jst(2026, 3, 6, 6, 5)),
        (21 * 3600 + 48 * 60, _jst(2026, 3, 6, 21, 48)),
        # GTFS's extended hours run past midnight into the next date.
        (24 * 3600 + 27 * 60, _jst(2026, 3, 7, 0, 27)),
    ],
)
def test_a_timetable_time_falls_on_its_service_dates_clock(seconds, expected):
    moment = service_day_moment_utc(date(2026, 3, 6), seconds)
    assert moment == expected
    assert moment.utcoffset() == timedelta(0)
    assert service_date_of(moment) == date(2026, 3, 6)


def test_the_seconds_fragment_names_the_expression_it_is_given():
    fragment = service_day_seconds_sql("x.secs")
    assert "x.secs" in fragment
    assert str(SERVICE_DAY_CUTOVER_HOURS * 3600) in fragment
