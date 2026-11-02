"""The daily-headway rows analyze stores, from the per-stop event times ClickHouse returns."""

from datetime import date
from statistics import median

from pipeline.headways import count_long_gaps, daily_headway_rows, pool_daily_gaps

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


def test_each_row_carries_the_moments_and_the_long_gap_count():
    pooled = {("R1", D1): [600.0, 600.0, 1800.0]}
    gaps = pooled[("R1", D1)]
    assert daily_headway_rows(7, pooled, {"R1": 600}) == [
        (7, "R1", D1, median(gaps), 3, sum(gaps), sum(g * g for g in gaps), count_long_gaps(gaps, 600)),
    ]


def test_a_route_without_a_scheduled_median_counts_long_gaps_against_none():
    pooled = {("R9", D1): [600.0, 1200.0]}
    (row,) = daily_headway_rows(7, pooled, {})
    assert row[-1] == count_long_gaps([600.0, 1200.0], None)


def test_a_group_with_no_gaps_is_left_out():
    assert daily_headway_rows(7, {("R1", D1): []}, {"R1": 600}) == []
