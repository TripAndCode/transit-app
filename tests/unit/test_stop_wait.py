from fractions import Fraction

import pytest

from pipeline.stop_wait import P50, P90, HourWait, hour_waits, judged, wait_quantile, wait_spans

H = 3600


def _minutes(value: Fraction) -> Fraction:
    return value / 60


def test_a_departure_every_ten_minutes_has_riders_wait_five_minutes_at_the_median():
    spans = wait_spans(list(range(0, 2 * H, 600)), 0, H)
    assert spans == [(0, 600)] * 6
    assert _minutes(wait_quantile(spans, P50)) == 5
    assert _minutes(wait_quantile(spans, P90)) == 9


def test_one_gap_of_an_hour_has_riders_wait_half_of_it_at_the_median():
    spans = wait_spans([0, H], 0, H)
    assert _minutes(wait_quantile(spans, P50)) == 30
    assert _minutes(wait_quantile(spans, P90)) == 54


def test_buses_running_in_pairs_double_the_median_wait():
    timetabled = list(range(0, 2 * H, 600))
    paired = sorted(t + (600 if (t // 600) % 2 == 0 else 0) for t in timetabled)
    assert _minutes(wait_quantile(wait_spans(timetabled, 0, H), P50)) == 5
    assert _minutes(wait_quantile(wait_spans(paired, 0, H), P50)) == 10


def test_riders_after_the_last_departure_have_none_to_wait_for():
    # Arrivals from 20 minutes on have no departure: only the first 20 count.
    assert wait_spans([600, 1200], 0, H) == [(0, 600), (0, 600)]


def test_a_departure_after_the_window_serves_its_last_riders():
    assert wait_spans([900, 4500], 0, H) == [(0, 900), (900, 3600)]


def test_departures_at_one_moment_make_no_empty_span():
    assert wait_spans([600, 600, 1200], 0, 1200) == [(0, 600), (0, 600)]


def test_a_quantile_falls_past_a_stretch_no_wait_lies_in():
    # Waits 0-60 for 60 s of arrivals and 600-660 for 60 more: none between.
    spans = [(0, 60), (600, 660)]
    assert wait_quantile(spans, P50) == 60
    assert wait_quantile(spans, Fraction(3, 4)) == 630


def test_a_quantile_of_no_arrival_is_refused():
    with pytest.raises(ValueError):
        wait_quantile([], P50)


def test_a_day_is_judged_for_an_hour_with_every_departure_that_can_serve_it():
    planned = {7: 4, 8: 6, 10: 2}
    assert judged({7: 4, 8: 6, 10: 2}, planned, 8)
    # Before: a late bus from 7 serves 8's riders.
    assert not judged({7: 3, 8: 6, 10: 2}, planned, 8)
    # After: the next departure is at 10, not 9.
    assert not judged({7: 4, 8: 6, 10: 1}, planned, 8)
    assert not judged({7: 4, 8: 5, 10: 2}, planned, 8)
    # More than planned passes; an hour further on is not read.
    assert judged({7: 5, 8: 7, 10: 2, 11: 0}, planned, 8)
    # The last hour of service has no next departure to need.
    assert judged({8: 6, 10: 2}, planned, 10)


def test_hour_waits_pool_the_judged_days_and_set_the_timetable_beside():
    secs = list(range(8 * H, 10 * H, 600))
    planned = {8: 6, 9: 6}
    on_time = (secs, [0] * len(secs))
    # Every even departure 10 minutes late, joining the next one.
    paired = (secs, [600 if i % 2 == 0 else 0 for i in range(len(secs))])
    # Missing a departure at 8: not judged for 8.
    short = (secs[1:], [0] * (len(secs) - 1))
    waits = hour_waits([on_time, paired, short], planned, [8])
    assert set(waits) == {8}
    wait = waits[8]
    assert wait.days == 2
    assert _minutes(wait.scheduled_p50_sec) == 5
    assert _minutes(wait.scheduled_p90_sec) == 9
    # Pooled, between the on-time day's 5 minutes and the paired day's 10.
    assert _minutes(wait.p50_sec) == Fraction(20, 3)


def test_both_sides_count_the_same_riders_when_the_last_bus_runs_early():
    # The last departure at 09:50 left at 09:45: riders from 09:45 on have
    # none to wait for as run, so neither side counts them. Each side has
    # 45 minutes of riders; the timetable's last 5 wait 5 to 10 minutes.
    secs = [9 * H + 600 * i for i in range(6)]
    delays = [0] * 5 + [-300]
    waits = hour_waits([(secs, delays)], {9: 6}, [9])
    assert waits[9] == HourWait(1, Fraction(270), Fraction(1065, 2), Fraction(330), Fraction(546))


def test_an_hour_with_no_judged_day_is_left_out():
    assert hour_waits([([8 * H], [0])], {8: 2}, [8]) == {}
    assert hour_waits([([], [])], {8: 1}, [8]) == {}
