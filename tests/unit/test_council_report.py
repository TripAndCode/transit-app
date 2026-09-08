"""Pure-logic tests for pipeline.reports.council -- the delay-certificate
export's scheduled -> actual clock-time arithmetic.
"""

from pipeline.reports.council import shift_time_str


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
