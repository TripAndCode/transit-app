"""Where a reload stops saving the outgoing timetable's planned trips: the day
before the incoming timetable's first, by the coverage rule the planned-trip
count applies within a capture's reach, and never anything a malformed feed
date can push out of range."""

from datetime import date, timedelta

import pytest

from pipeline.planned_trips import CAPTURE_LOOKBACK_DAYS, _reload_through, capture_before_reload
from pipeline.static_loader import _covered_ranges

TODAY = date(2026, 4, 15)
WINDOW_START = TODAY - timedelta(days=CAPTURE_LOOKBACK_DAYS - 1)


def _through(calendar=(), dates=()):
    rows = [["ALL", *([True] * 7), start, end] for start, end in calendar]
    return _reload_through(TODAY, _covered_ranges(rows, [["ALL", raw, kind] for raw, kind in dates]))


@pytest.mark.parametrize(
    ("first", "through"),
    [
        (date(2026, 4, 15), date(2026, 4, 14)),  # takes effect today: today is the incoming plan's
        (date(2026, 4, 18), date(2026, 4, 17)),  # three days on: those days still run on the outgoing one
        (date(2026, 4, 12), date(2026, 4, 11)),  # reaches back before today
    ],
)
def test_the_outgoing_timetable_keeps_the_days_before_the_incoming_one_starts(first, through):
    assert _through(calendar=[(first, date(2026, 10, 14))]) == through


def test_a_calendar_dates_day_of_any_kind_can_be_the_first():
    calendar = [(date(2026, 4, 20), date(2026, 10, 14))]
    assert _through(calendar, [("20260413", "1")]) == date(2026, 4, 12)
    # A removal still says something about the day, so the timetable covers it.
    assert _through(calendar, [("20260412", "2")]) == date(2026, 4, 11)


def test_a_malformed_calendar_dates_day_covers_nothing():
    calendar = [(date(2026, 4, 20), date(2026, 10, 14))]
    malformed = [(raw, "1") for raw in ("2026041", "20260231", "２０２６０４０１", "", None, " 20260401")]
    assert _through(calendar, malformed) == date(2026, 4, 19)


def test_a_service_whose_range_ends_before_it_starts_covers_nothing():
    reversed_range = (date(2026, 4, 1), date(2026, 3, 1))
    assert _through([reversed_range, (date(2026, 4, 20), date(2026, 10, 14))]) == date(2026, 4, 19)
    assert _through([reversed_range]) == TODAY


def test_a_date_out_of_any_capture_s_reach_moves_nothing():
    calendar = [(date(2026, 4, 20), date(2026, 10, 14))]
    assert _through(calendar, [("00010101", "1")]) == date(2026, 4, 19)
    assert _through([(date(2020, 1, 1), date(2020, 12, 31))]) == TODAY
    # A service running since year 1 covers the whole window, leaving nothing
    # for the outgoing timetable.
    assert _through([(date(1, 1, 1), date(2027, 1, 1))]) == WINDOW_START - timedelta(days=1)
    assert _through(dates=[("99991231", "1")]) == TODAY + timedelta(days=CAPTURE_LOOKBACK_DAYS)


def test_an_incoming_timetable_covering_no_day_leaves_the_outgoing_one_today():
    assert _through() == TODAY


class _RecordingConn:
    def __init__(self):
        self.params = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if params is not None:
            self.params = params
        self.rowcount = 0

    def commit(self):
        pass


def test_the_reload_capture_takes_extreme_dates_without_raising():
    conn = _RecordingConn()
    extremes = [(date.min, date.min), (date.max, date.max)]
    assert capture_before_reload(conn, 1, extremes, today=TODAY) == 0
    assert conn.params["to_date"] == TODAY + timedelta(days=CAPTURE_LOOKBACK_DAYS)


def test_the_reload_capture_never_raises():
    def _broken():
        raise OverflowError("a feed date out of range")
        yield

    assert capture_before_reload(_RecordingConn(), 1, _broken(), today=TODAY) is None
