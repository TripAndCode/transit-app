"""Pure-logic tests for pipeline.reports.council -- the delay-certificate
export's scheduled -> actual clock-time arithmetic, and which filters split the
council summary's on-time figures from its agency-wide ones.
"""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports.council import on_time_filter_narrows, shift_time_str


def test_shift_time_str_within_same_day_adds_seconds():
    assert shift_time_str("08:15:00", 90) == "08:16:30"


def test_shift_time_str_handles_missing_seconds_in_input():
    # normalize_departure_time (pipeline/strategies/_time.py) can leave off
    # seconds ("HH:MM") when GTFS's own departure_time did -- must not raise.
    assert shift_time_str("08:15", 30) == "08:15:30"


def test_shift_time_str_negative_delay_moves_earlier():
    assert shift_time_str("08:15:00", -30) == "08:14:30"


def test_shift_time_str_crossing_midnight_forward_gets_day_marker():
    # 23:50:00 + 20 minutes (1200s) = 00:10:00 the next day.
    assert shift_time_str("23:50:00", 1200) == "00:10:00(+1日)"


def test_shift_time_str_crossing_midnight_backward_gets_day_marker():
    # 00:05:00 - 10 minutes (600s) = 23:55:00 the previous day.
    assert shift_time_str("00:05:00", -600) == "23:55:00(-1日)"


def test_shift_time_str_no_shift_at_all_has_no_day_marker():
    assert shift_time_str("12:00:00", 0) == "12:00:00"


def test_shift_time_str_names_the_day_marker_in_english():
    assert shift_time_str("23:50:00", 1200, locale="en") == "00:10:00 (+1 day)"
    assert shift_time_str("00:05:00", -600, locale="en") == "23:55:00 (-1 day)"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, False),
        ({"routes": ("R1",)}, True),
        ({"dow": "weekday"}, True),
        ({"time_band": "morning"}, True),
        ({"service": "平日"}, True),
        # Neither figure group honours hour/stop/dir, so they cannot split them.
        ({"hour": (7, 9)}, False),
        ({"stop": "S1", "direction": 0}, False),
    ],
)
def test_on_time_filter_narrows_past_the_agency_wide_figures(overrides, expected):
    ctx = RangeCtx(from_date=date(2026, 9, 1), to_date=date(2026, 9, 30), **overrides)
    assert on_time_filter_narrows(ctx) is expected
