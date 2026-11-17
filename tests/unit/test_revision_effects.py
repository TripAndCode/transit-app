"""What a revision did to each route (pipeline.reports.revision_effects)."""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.reports.revision_effects import (
    Plan,
    RouteChange,
    RouteDay,
    StopGaps,
    Tally,
    Windows,
    class_tallies,
    main_stop,
    plan,
    pool,
    route_changes,
    route_classes,
    route_counts_of,
    stop_counts_of,
)
from pipeline.reports.timetable_diff import Revision, Run

REVISION = Revision(
    Run(date(2026, 1, 1), date(2026, 3, 31), "v1", "c1", "d1"),
    Run(date(2026, 4, 1), date(2026, 9, 30), "v2", "c2", "d2"),
)


def test_the_main_stop_is_the_one_departed_most_before_lowest_id_on_a_tie():
    before = {"B": StopGaps(10, 1800), "A": StopGaps(10, 1200), "C": StopGaps(4, 3600)}
    assert main_stop(before, {"C": StopGaps(20, 600)}) == "A"


def test_a_route_new_after_the_revision_is_measured_at_its_after_sides_main_stop():
    assert main_stop({}, {"X": StopGaps(3, 3600), "Y": StopGaps(5, 2400)}) == "Y"
    assert main_stop({}, {}) is None


def test_the_gap_is_compared_at_one_stop_on_both_sides():
    before = {"weekday": {"R1": RouteDay(10, 75600)}}
    after = {"weekday": {"R1": RouteDay(6, 72000)}}
    # The after side's busiest stop is another one; the gap is still read at the before side's.
    before_stops = {"weekday": {"R1": {"S1": StopGaps(10, 3600), "S2": StopGaps(8, 3600)}}}
    after_stops = {"weekday": {"R1": {"S1": StopGaps(5, 7200), "S2": StopGaps(6, 5400)}}}
    [change] = route_changes(before, after, before_stops, after_stops, ["weekday"])
    assert change.main_stop == "S1"
    assert (change.longest_gap_before, change.longest_gap_after) == (3600, 7200)
    assert (change.trips_before, change.trips_after, change.last_before, change.last_after) == (10, 6, 75600, 72000)


def test_a_route_gone_after_the_revision_keeps_its_before_side_and_none_after():
    before = {"saturday": {"R9": RouteDay(4, 60000)}}
    before_stops = {"saturday": {"R9": {"S1": StopGaps(4, 9000)}}}
    [change] = route_changes(before, {}, before_stops, {}, ["saturday"])
    assert (change.trips_before, change.trips_after, change.last_after, change.longest_gap_after) == (4, 0, None, None)


def test_only_the_day_types_given_are_compared():
    before = {"weekday": {"R1": RouteDay(1, 1)}, "saturday": {"R1": RouteDay(1, 1)}}
    assert {c.day_type for c in route_changes(before, before, {}, {}, ["weekday"])} == {"weekday"}


def test_the_archive_rows_read_by_day_type_in_their_order():
    routes = route_counts_of([(1, "R1", 5, 72000), (3, "R1", 2, 50000)])
    assert routes["weekday"]["R1"] == RouteDay(5, 72000)
    assert routes["sunday_holiday"]["R1"] == RouteDay(2, 50000)
    stops = stop_counts_of([(2, "R1", [("S1", 1, None), ("S2", 3, 1800)])])
    assert stops["saturday"]["R1"] == {"S1": StopGaps(1, None), "S2": StopGaps(3, 1800)}


def _change(route: str, day_type, before: int, after: int) -> RouteChange:
    return RouteChange(route, day_type, before, after, None, None, None, None, None)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [(10, 9, "cut"), (10, 11, "added"), (10, 10, "unchanged"), (0, 3, "new"), (3, 0, "removed")],
)
def test_a_route_is_classed_by_its_trips(before, after, expected):
    assert route_classes([_change("R", "weekday", before, after)]) == {"R": expected}


def test_a_route_is_classed_on_the_day_type_compared():
    # Cut only on Saturdays: unchanged on weekdays.
    weekday = route_classes([_change("R", "weekday", 10, 10)])
    saturday = route_classes([_change("R", "saturday", 8, 4)])
    assert (weekday, saturday) == ({"R": "unchanged"}, {"R": "cut"})


def test_route_timetables_class_each_day_type_on_its_own_routes():
    from pipeline.reports.revision_effects import RouteTimetables

    timetables = RouteTimetables(
        ("weekday", "saturday"),
        (_change("R", "weekday", 10, 10), _change("R", "saturday", 8, 4), _change("S", "saturday", 0, 2)),
    )
    changes, classes = timetables.on("saturday")
    assert [c.route_code for c in changes] == ["R", "S"]
    assert classes == {"R": "cut", "S": "new"}
    assert timetables.on("weekday")[1] == {"R": "unchanged"}


LONG_AGO = date(2025, 1, 1)


def test_four_whole_weeks_on_each_side_once_the_aggregates_reach_them():
    found = plan(REVISION, LONG_AGO, date(2026, 4, 28))
    assert found == Plan(
        4,
        None,
        date(2026, 4, 28),
        Windows(4, (date(2026, 3, 4), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 28))),
    )
    assert found.complete


def test_a_day_short_of_a_week_after_counts_one_week_fewer_and_is_not_complete():
    found = plan(REVISION, LONG_AGO, date(2026, 4, 27))
    assert (found.cap, found.windows.weeks, found.complete) == (4, 3, False)
    assert found.windows.before == (date(2026, 3, 11), date(2026, 3, 31))
    assert found.windows.after == (date(2026, 4, 1), date(2026, 4, 21))
    assert found.full_through == date(2026, 4, 28)


def test_no_window_before_a_whole_week_after_or_with_no_aggregates():
    assert plan(REVISION, LONG_AGO, date(2026, 4, 6)).windows is None
    assert plan(REVISION, None, None).windows is None
    assert plan(REVISION, None, None).full_through == date(2026, 4, 28)
    assert plan(REVISION, LONG_AGO, date(2026, 4, 7)).windows.weeks == 1


def test_a_short_timetable_before_caps_both_sides_for_good():
    # In force 14 days: two weeks is all there will ever be.
    short = REVISION._replace(before=REVISION.before._replace(first=date(2026, 3, 18)))
    found = plan(short, LONG_AGO, date(2026, 6, 1))
    assert (found.cap, found.capped_by, found.full_through) == (2, "timetable", date(2026, 4, 14))
    assert found.windows.before == (date(2026, 3, 18), date(2026, 3, 31))
    assert found.complete


def test_a_timetable_after_that_ends_within_four_weeks_caps_the_comparison():
    brief = REVISION._replace(after=REVISION.after._replace(last=date(2026, 4, 15)))
    found = plan(brief, LONG_AGO, date(2026, 4, 10))
    assert (found.cap, found.capped_by, found.full_through) == (2, "timetable", date(2026, 4, 14))
    assert (found.windows.weeks, found.complete) == (1, False)
    assert plan(brief, LONG_AGO, date(2026, 6, 1)).complete


def test_a_timetable_in_force_under_a_week_leaves_no_comparable_week():
    tiny = REVISION._replace(before=REVISION.before._replace(first=date(2026, 3, 27)))
    found = plan(tiny, LONG_AGO, date(2026, 6, 1))
    assert (found.cap, found.capped_by, found.full_through, found.windows) == (0, "timetable", None, None)
    assert not found.complete


def test_the_agencys_first_aggregated_day_caps_the_side_before():
    # Aggregated from 2026-03-20: twelve days before the revision, one whole week.
    found = plan(REVISION, date(2026, 3, 20), date(2026, 6, 1))
    assert (found.cap, found.capped_by, found.full_through) == (1, "history", date(2026, 4, 7))
    assert found.windows.before == (date(2026, 3, 25), date(2026, 3, 31))
    assert found.complete
    assert plan(REVISION, date(2026, 3, 30), date(2026, 6, 1)).windows is None


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


def test_tallies_add_by_count():
    assert Tally(4, 3, 120).plus(Tally(6, 1, -60)) == Tally(10, 4, 60)


def test_a_tally_reads_its_rate_and_mean_delay():
    assert Tally(4, 3, 120).on_time_pct == 75
    assert Tally(4, 3, 120).mean_delay_sec == 30
    assert Tally().mean_delay_sec is None
