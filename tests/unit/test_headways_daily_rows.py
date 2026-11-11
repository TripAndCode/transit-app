"""The daily-headway rows analyze stores, from the per-stop event times ClickHouse returns."""

from datetime import date
from statistics import median

from pipeline.headways import (
    ScheduledHeadways,
    count_long_gaps,
    daily_headway_rows,
    dominant_contents,
    pool_daily_gaps,
    schedules,
    thresholds,
)

D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)


def test_gaps_pool_per_route_and_day_across_stops():
    rows = [
        ("R1", "s1", D1, [600, 1200, 1800]),
        ("R1", "s2", D1, [660, 1260]),
        ("R1", "s1", D2, [600, 900]),
        ("R2", "s1", D1, [600, 1500]),
    ]
    assert pool_daily_gaps(rows) == {
        ("R1", D1): [600, 600, 600],
        ("R1", D2): [300],
        ("R2", D1): [900],
    }


def test_zero_and_negative_gaps_are_dropped():
    rows = [("R1", "s1", D1, [600, 600, 1200])]
    assert pool_daily_gaps(rows) == {("R1", D1): [600]}


def _not_kept(route, day):
    return None


def test_each_row_carries_the_moments_and_the_long_gap_count():
    pooled = {("R1", D1): [600.0, 600.0, 1800.0]}
    gaps = pooled[("R1", D1)]
    assert daily_headway_rows(7, pooled, lambda route, day: {"R1": 600}.get(route), _not_kept) == [
        (
            7,
            "R1",
            D1,
            median(gaps),
            3,
            sum(gaps),
            sum(g * g for g in gaps),
            count_long_gaps(gaps, 600),
            None,
            None,
            None,
            None,
        ),
    ]


def test_a_route_without_a_scheduled_median_counts_no_long_gaps():
    pooled = {("R9", D1): [600.0, 1200.0]}
    (row,) = daily_headway_rows(7, pooled, lambda route, day: None, _not_kept)
    assert row[7] == 0


def test_a_group_with_no_gaps_is_left_out():
    assert daily_headway_rows(7, {("R1", D1): []}, lambda route, day: 600, _not_kept) == []


def test_the_dominant_content_is_the_one_most_events_carry_across_stops():
    rows = [
        ("R1", "s1", D1, [600, 1200], (["a"], [2])),
        ("R1", "s2", D1, [660], (["b"], [1])),
        ("R2", "s1", D1, [600], (["b"], [1])),
    ]
    assert dominant_contents(rows, lambda stem: stem) == {("R1", D1): "a", ("R2", D1): "b"}


def test_a_tie_goes_to_the_lowest_content():
    rows = [("R1", "s1", D1, [600], (["b"], [1])), ("R1", "s2", D1, [660], (["a"], [1]))]
    assert dominant_contents(rows, lambda stem: stem) == {("R1", D1): "a"}


def test_each_events_name_is_mapped_to_its_content_before_counting():
    rows = [("R1", "s1", D1, [600, 1200, 1800], (["", "old", "x"], [1, 1, 1]))]
    loaded = {"old": "c_old"}
    assert dominant_contents(rows, lambda stem: loaded.get(stem, "c_loaded")) == {("R1", D1): "c_loaded"}


def test_the_pooled_gaps_ignore_the_names_column():
    assert pool_daily_gaps([("R1", "s1", D1, [600, 1200], (["a"], [2]))]) == {("R1", D1): [600]}


def test_a_name_counts_as_many_events_as_carry_it():
    rows = [("R1", "s1", D1, [600, 1200, 1800], (["a", "b"], [1, 2]))]
    assert dominant_contents(rows, lambda stem: stem) == {("R1", D1): "b"}


def _median(sec: float) -> ScheduledHeadways:
    return ScheduledHeadways(sec, None, None)


def test_a_threshold_is_the_dominant_timetables_median():
    median_of = thresholds(
        {("R1", D1): "a"}, {("a", "R1"): _median(600.0), ("loaded", "R1"): _median(1200.0)}, "loaded"
    )
    assert median_of("R1", D1) == 600.0


def test_a_timetable_with_no_kept_medians_takes_the_loaded_ones():
    median_of = thresholds({("R1", D1): "old"}, {("loaded", "R1"): _median(1200.0)}, "loaded")
    assert median_of("R1", D1) == 1200.0


def test_a_route_its_timetable_gives_no_median_has_none():
    median_of = thresholds({("R1", D1): "a"}, {("a", "R2"): _median(600.0)}, "loaded")
    assert median_of("R1", D1) is None


def test_each_day_reads_the_scheduled_headways_of_its_own_timetable_and_no_other():
    kept = {
        ("a", "R1"): ScheduledHeadways(720.0, 8640.0, 6220800.0),
        ("b", "R1"): ScheduledHeadways(240.0, 23040.0, 68428800.0),
        ("b", "R2"): ScheduledHeadways(None, None, None),
    }
    dominant = {("R1", 1): "a", ("R1", 2): "b", ("R2", 2): "b", ("R3", 2): "b", ("R1", 3): "c"}
    schedule_of = schedules(dominant, kept)

    assert schedule_of("R1", 1) == kept[("a", "R1")]
    assert schedule_of("R1", 2) == kept[("b", "R1")]
    assert schedule_of("R2", 2) == ScheduledHeadways(None, None, None)
    # A route the day's timetable keeps nothing for has no gap in it.
    assert schedule_of("R3", 2) == ScheduledHeadways(None, None, None)
    # A timetable keeping nothing judges nothing: no other one stands in.
    assert schedule_of("R1", 3) is None


def test_a_daily_row_carries_its_own_timetables_high_frequency_test_and_scheduled_sums():
    pooled = {("R1", 1): [600.0, 600.0], ("R1", 2): [600.0, 1500.0], ("R2", 1): [900.0, 900.0]}
    schedule = {
        ("R1", 1): ScheduledHeadways(720.0, 1440.0, 1036800.0),
        ("R1", 2): None,
        ("R2", 1): ScheduledHeadways(None, None, None),
    }
    rows = daily_headway_rows(9, pooled, lambda _route, _day: 600.0, lambda route, day: schedule[(route, day)])

    assert rows == [
        (9, "R1", 1, 600.0, 2, 1200.0, 720000.0, 0, 720.0, True, 1440.0, 1036800.0),
        # Not judged: its timetable keeps no scheduled headways.
        (9, "R1", 2, 1050.0, 2, 2100.0, 2610000.0, 1, None, None, None, None),
        # Judged, and not high-frequency: its timetable gives the route no gap.
        (9, "R2", 1, 900.0, 2, 1800.0, 1620000.0, 0, None, False, None, None),
    ]
