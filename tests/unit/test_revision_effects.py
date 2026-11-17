"""What a revision did to each route (pipeline.reports.revision_effects)."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.reports.revision_effects import (
    RouteChange,
    RouteDay,
    StopGaps,
    Tally,
    Windows,
    class_tallies,
    full_through,
    main_stop,
    pool,
    route_changes,
    route_classes,
    route_counts_of,
    stop_counts_of,
    windows,
)
from pipeline.reports.timetable_diff import Revision, Run

REVISION = Revision(
    Run(date(2026, 1, 1), date(2026, 3, 31), "v1", "c1", "d1"),
    Run(date(2026, 4, 1), date(2026, 9, 30), "v2", "c2", "d2"),
)


# ── main stop and route changes ─────────────────────────────────────────────


def test_the_main_stop_is_the_one_departed_most_before_lowest_id_on_a_tie():
    before = {"B": StopGaps(10, 1800), "A": StopGaps(10, 1200), "C": StopGaps(4, 3600)}
    assert main_stop(before, {"C": StopGaps(20, 600)}) == "A"


def test_a_route_new_after_the_revision_is_measured_at_its_after_sides_main_stop():
    assert main_stop({}, {"X": StopGaps(3, 3600), "Y": StopGaps(5, 2400)}) == "Y"
    assert main_stop({}, {}) is None


def test_the_gap_is_compared_at_one_stop_on_both_sides():
    before = {"weekday": {"R1": RouteDay(10, 21600, 75600)}}
    after = {"weekday": {"R1": RouteDay(6, 21600, 72000)}}
    # The after side's busiest stop is another one; the gap is still read at the before side's.
    before_stops = {"weekday": {"R1": {"S1": StopGaps(10, 3600), "S2": StopGaps(8, 3600)}}}
    after_stops = {"weekday": {"R1": {"S1": StopGaps(5, 7200), "S2": StopGaps(6, 5400)}}}
    [change] = route_changes(before, after, before_stops, after_stops, ["weekday"])
    assert change.main_stop == "S1"
    assert (change.main_departures_before, change.main_departures_after) == (10, 5)
    assert (change.longest_gap_before, change.longest_gap_after) == (3600, 7200)
    assert (change.trips_before, change.trips_after, change.last_before, change.last_after) == (10, 6, 75600, 72000)


def test_a_route_gone_after_the_revision_keeps_its_before_side_and_none_after():
    before = {"saturday": {"R9": RouteDay(4, 30000, 60000)}}
    before_stops = {"saturday": {"R9": {"S1": StopGaps(4, 9000)}}}
    [change] = route_changes(before, {}, before_stops, {}, ["saturday"])
    assert (change.trips_before, change.trips_after, change.first_after, change.longest_gap_after) == (4, 0, None, None)
    assert change.main_departures_after == 0


def test_only_the_day_types_given_are_compared():
    before = {"weekday": {"R1": RouteDay(1, 1, 1)}, "saturday": {"R1": RouteDay(1, 1, 1)}}
    assert {c.day_type for c in route_changes(before, before, {}, {}, ["weekday"])} == {"weekday"}


def test_the_archive_rows_read_by_day_type_in_their_order():
    routes = route_counts_of([(1, "R1", 5, 21600, 72000), (3, "R1", 2, 30000, 50000)])
    assert routes["weekday"]["R1"] == RouteDay(5, 21600, 72000)
    assert routes["sunday_holiday"]["R1"] == RouteDay(2, 30000, 50000)
    stops = stop_counts_of([(2, "R1", "S1", 1, None), (2, "R1", "S2", 3, 1800)])
    assert stops["saturday"]["R1"] == {"S1": StopGaps(1, None), "S2": StopGaps(3, 1800)}


# ── route classes ───────────────────────────────────────────────────────────


def _change(route: str, day_type, before: int, after: int) -> RouteChange:
    return RouteChange(route, day_type, before, after, None, None, None, None, None, 0, 0, None, None)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [(10, 9, "cut"), (10, 11, "added"), (10, 10, "unchanged"), (0, 3, "new"), (3, 0, "removed")],
)
def test_a_route_is_classed_by_its_weeks_trips(before, after, expected):
    assert route_classes([_change("R", "weekday", before, after)]) == {"R": expected}


def test_a_weekday_counts_five_times_against_one_weekend_day():
    # Weekday +1 trip (5 a week) outweighs Saturday -4.
    changes = [_change("R", "weekday", 10, 11), _change("R", "saturday", 8, 4)]
    assert route_classes(changes) == {"R": "added"}
    changes = [_change("R", "weekday", 10, 11), _change("R", "saturday", 8, 2)]
    assert route_classes(changes) == {"R": "cut"}


# ── windows ─────────────────────────────────────────────────────────────────


def test_four_whole_weeks_on_each_side_once_the_aggregates_reach_them():
    found = windows(REVISION, date(2026, 4, 28))
    assert found == Windows(4, (date(2026, 3, 4), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 28)))
    assert found.complete
    assert full_through(REVISION) == date(2026, 4, 28)


def test_a_day_short_of_a_week_after_counts_one_week_fewer():
    found = windows(REVISION, date(2026, 4, 27))
    assert found is not None and found.weeks == 3 and not found.complete
    assert found.before == (date(2026, 3, 11), date(2026, 3, 31))
    assert found.after == (date(2026, 4, 1), date(2026, 4, 21))


def test_no_window_before_a_whole_week_after_or_with_no_aggregates():
    assert windows(REVISION, date(2026, 4, 6)) is None
    assert windows(REVISION, None) is None
    assert windows(REVISION, date(2026, 4, 7)).weeks == 1


def test_a_short_timetable_before_bounds_both_sides():
    short = REVISION._replace(before=REVISION.before._replace(first=date(2026, 3, 18)))
    found = windows(short, date(2026, 6, 1))
    assert found.weeks == 2
    assert found.before == (date(2026, 3, 18), date(2026, 3, 31))


def test_the_after_side_stops_where_its_timetable_does():
    brief = REVISION._replace(after=REVISION.after._replace(last=date(2026, 4, 15)))
    assert windows(brief, date(2026, 6, 1)).weeks == 2


# ── pooling ─────────────────────────────────────────────────────────────────

_ONE_WEEK = Windows(1, (date(2026, 3, 25), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 7)))


def test_dates_pool_by_side_and_weekday_rule_day_type():
    rows = [
        (date(2026, 3, 31), "R1", 10, 8, 600),  # Tuesday, before
        (date(2026, 3, 30), "R1", 10, 6, 900),  # Monday, before
        (date(2026, 3, 28), "R1", 4, 4, 0),  # Saturday, before
        (date(2026, 4, 1), "R1", 10, 10, 100),  # Wednesday, after
        (date(2026, 4, 5), "R1", 3, 1, 300),  # Sunday, after
        (date(2026, 3, 24), "R1", 99, 0, 0),  # outside both windows
        (date(2026, 4, 8), "R1", 99, 0, 0),
    ]
    pooled = pool(rows, _ONE_WEEK)
    assert pooled[("before", "weekday", "R1")] == Tally(20, 14, 1500)
    assert pooled[("before", "saturday", "R1")] == Tally(4, 4, 0)
    assert pooled[("after", "weekday", "R1")] == Tally(10, 10, 100)
    assert pooled[("after", "sunday_holiday", "R1")] == Tally(3, 1, 300)
    assert len(pooled) == 4


def test_a_public_holiday_pools_with_sundays():
    spans = Windows(1, (date(2026, 4, 22), date(2026, 4, 28)), (date(2026, 4, 29), date(2026, 5, 5)))
    pooled = pool([(date(2026, 4, 29), "R1", 5, 5, 0)], spans)  # 昭和の日, a Wednesday
    assert ("after", "sunday_holiday", "R1") in pooled


def test_a_class_pools_exact_counts_of_routes_observed_on_both_sides():
    observed = {
        ("before", "weekday", "big"): Tally(1000, 500, 0),
        ("after", "weekday", "big"): Tally(1000, 900, 0),
        ("before", "weekday", "small"): Tally(10, 10, 0),
        ("after", "weekday", "small"): Tally(10, 0, 0),
        ("before", "weekday", "gone"): Tally(500, 0, 0),
    }
    classes = {"big": "cut", "small": "cut", "gone": "cut", "flat": "unchanged"}
    by_class = {c.route_class: c for c in class_tallies(observed, classes, "weekday")}
    cut = by_class["cut"]
    assert cut.routes == 2
    assert (cut.before, cut.after) == (Tally(1010, 510, 0), Tally(1010, 900, 0))
    # Pooled, not the mean of route percentages (which would be 75 → 45).
    assert round(cut.before.on_time_pct, 1) == 50.5
    assert round(cut.after.on_time_pct, 1) == 89.1
    assert by_class["unchanged"].routes == 0
    assert by_class["unchanged"].before.on_time_pct is None


def test_a_tally_reads_its_rate_and_mean_delay():
    assert Tally(4, 3, 120).on_time_pct == 75
    assert Tally(4, 3, 120).mean_delay_sec == 30
    assert Tally().mean_delay_sec is None
