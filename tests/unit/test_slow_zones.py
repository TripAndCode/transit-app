"""The slow-zone arithmetic: speed, a slow week, the streak and minutes lost."""

from datetime import date

import pytest

from pipeline.reports.slow_zones import (
    MIN_SLOW_WEEKS,
    MIN_WEEK_SAMPLES,
    WINDOW_WEEKS,
    SegmentWeek,
    SlowZones,
    SlowZoneSegment,
    is_slow_week,
    minutes_lost_per_day,
    slow_streak,
    speed_kmh,
    window_dates,
    window_weeks,
)


def week(excess: float, scheduled: float = 120.0, samples: int = 30, days: int = 5) -> SegmentWeek:
    return SegmentWeek(
        samples=samples,
        days=days,
        run_p50_sec=scheduled + excess,
        run_p85_sec=scheduled + excess + 30,
        scheduled_run_sec=scheduled,
    )


SLOW = week(90)
FINE = week(10)


def test_speed_is_distance_over_the_median_run():
    """900 m in 120 s is 7.5 m/s, 27 km/h."""
    assert speed_kmh(900.0, 120.0) == pytest.approx(27.0)


@pytest.mark.parametrize("distance, run", [(None, 120.0), (900.0, None), (0.0, 120.0), (900.0, 0.0), (900.0, -5.0)])
def test_speed_needs_a_positive_distance_and_run(distance, run):
    assert speed_kmh(distance, run) is None


def test_excess_is_the_median_over_the_timetable():
    assert week(75).excess_sec == pytest.approx(75.0)
    assert SegmentWeek(30, 5, None, None, 120.0).excess_sec is None


@pytest.mark.parametrize("excess, slow", [(59, False), (59.9, False), (60, True), (61, True)])
def test_a_week_is_slow_from_a_minute_over_the_timetable(excess, slow):
    assert is_slow_week(week(excess)) is slow


@pytest.mark.parametrize("scheduled, slow", [(59, False), (60, True)])
def test_a_segment_scheduled_under_a_minute_is_never_slow(scheduled, slow):
    """A timetable to the minute makes a sub-minute segment's excess rounding."""
    assert is_slow_week(week(120, scheduled=scheduled)) is slow


@pytest.mark.parametrize("samples, slow", [(MIN_WEEK_SAMPLES - 1, False), (MIN_WEEK_SAMPLES, True)])
def test_a_week_needs_enough_runs(samples, slow):
    assert is_slow_week(week(120, samples=samples)) is slow


def test_a_week_without_a_scheduled_time_or_runs_is_not_slow():
    assert is_slow_week(None) is False
    assert is_slow_week(SegmentWeek(30, 5, 300.0, 330.0, None)) is False


def test_the_streak_counts_the_newest_slow_weeks_in_a_row():
    assert slow_streak([FINE, SLOW, SLOW, SLOW]) == 3
    assert slow_streak([SLOW, SLOW, FINE, SLOW, SLOW]) == 2
    assert slow_streak([SLOW, SLOW, SLOW, FINE]) == 0
    assert slow_streak([]) == 0


def test_a_week_not_observed_ends_the_streak():
    assert slow_streak([SLOW, SLOW, None, SLOW]) == 1
    assert slow_streak([SLOW, SLOW, SLOW, None]) == 0


def _segment(weeks):
    return SlowZoneSegment("A", "B", None, None, None, None, None, None, None, [], weeks)


def test_a_slow_zone_takes_three_slow_weeks():
    assert MIN_SLOW_WEEKS == 3
    assert _segment([FINE, SLOW, SLOW]).is_slow_zone is False
    assert _segment([FINE, SLOW, SLOW, SLOW]).is_slow_zone is True


def test_slow_zones_list_the_longest_standing_first_not_by_minutes_lost():
    long_mild = SlowZoneSegment("A", "B", None, None, None, None, None, None, None, [], [week(61)] * 5)
    short_severe = SlowZoneSegment("C", "D", None, None, None, None, None, None, None, [], [FINE, *[week(600)] * 3])
    fine = SlowZoneSegment("E", "F", None, None, None, None, None, None, None, [], [FINE] * 4)
    zones = SlowZones(True, None, None, "weekday", "morning", [], [short_severe, fine, long_mild]).slow_zones
    assert [z.from_stop_id for z in zones] == ["A", "C"]


def test_minutes_lost_is_the_excess_times_runs_per_day():
    """90 s over the timetable on 40 runs over 5 days: 8 runs a day lose 12 minutes."""
    assert minutes_lost_per_day(week(90, samples=40, days=5)) == pytest.approx(12.0)


def test_a_segment_faster_than_the_timetable_loses_nothing():
    assert minutes_lost_per_day(week(-45)) == 0.0


def test_minutes_lost_needs_a_day_with_runs():
    assert minutes_lost_per_day(week(90, days=0)) is None


def test_the_window_is_the_complete_weeks_before_the_last_day():
    """Wednesday 2026-04-15: its week is not over, so the newest week is
    Monday 2026-04-06 to Sunday 2026-04-12."""
    weeks = window_weeks(date(2026, 4, 15))
    assert len(weeks) == WINDOW_WEEKS
    assert weeks[-1] == date(2026, 4, 6)
    assert weeks[0] == date(2026, 2, 16)
    assert all(w.isoweekday() == 1 for w in weeks)


def test_a_sunday_completes_its_own_week():
    assert window_weeks(date(2026, 4, 12))[-1] == date(2026, 4, 6)
    assert window_weeks(date(2026, 4, 13))[-1] == date(2026, 4, 6)


def test_the_day_type_picks_dates_by_the_calendar():
    """Golden Week 2026: Wednesday 4/29 is 昭和の日, a holiday."""
    weeks = [date(2026, 4, 27)]
    assert window_dates(weeks, "weekday") == [date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 30), date(2026, 5, 1)]
    assert window_dates(weeks, "weekend_holiday") == [date(2026, 4, 29), date(2026, 5, 2), date(2026, 5, 3)]


def test_the_endpoint_takes_exactly_the_stored_bands():
    from typing import get_args

    from api.range import TIME_BAND_RANGES
    from api.routers.segments import TimeBand

    assert set(get_args(TimeBand)) == set(TIME_BAND_RANGES)
