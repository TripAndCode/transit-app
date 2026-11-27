"""scripts/validate_vp_predictions.py: observed stop passages reconstructed
from archived VehiclePositions, and the feed's kept TripUpdates prediction
held against them, with no database or network.

The passage tests lay synthetic tracks out in metres east and north of a base
point. The scenario tests run a declared operation through the collector's
archives and hold the tool's figures to ``tests.fixtures.scenario_oracle``,
which computes the prediction error from the truth alone.
"""

from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tarfile
import zipfile
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from scripts import validate_vp_predictions as vv
from tests.fixtures import scenario_oracle as oracle
from tests.fixtures.gtfs_rt import (
    stop_time_update,
    trip_update,
    trip_update_feed,
    vehicle_descriptor,
    vehicle_position_entity,
    vehicle_position_feed,
)
from tests.fixtures.scenario import (
    FeedModel,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    TripRun,
    polls,
    static_gtfs_zip,
    vehicle_positions,
)
from tests.pipeline.test_scenarios import imperfect_feed

UTC = timezone.utc
BASE_LAT, BASE_LON = 34.4, 132.45
# 09:00 JST on 2026-11-20.
T0 = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)


def _ll(east_m: float, north_m: float) -> tuple[float, float]:
    lat = BASE_LAT + north_m / vv.METERS_PER_DEGREE
    lon = BASE_LON + east_m / (vv.METERS_PER_DEGREE * math.cos(math.radians(BASE_LAT)))
    return lat, lon


def _fix(sec: float, east_m: float, north_m: float = 0.0) -> vv.Fix:
    return vv.Fix(T0 + timedelta(seconds=sec), *_ll(east_m, north_m))


def _stop(seq: int, east_m: float, north_m: float = 0.0, *, at: float = 0.0, final: bool = False) -> vv.StopPoint:
    return vv.StopPoint(seq, *_ll(east_m, north_m), T0 + timedelta(seconds=at), final)


def _line(start_sec: int, end_sec: int, step: int, east_at) -> list[vv.Fix]:
    return [_fix(t, east_at(t)) for t in range(start_sec, end_sec + 1, step)]


def _secs(found: dict[int, datetime]) -> dict[int, float]:
    return {seq: (at - T0).total_seconds() for seq, at in found.items()}


def test_a_stop_between_two_sparse_fixes_is_passed_where_the_track_crosses_it():
    """At 10 m/s, polled every 20 s: the fixes sit 200 m apart and neither is
    near the stop at 530 m, which the bus passes at 53 s."""
    track = _line(0, 120, 20, lambda t: 10 * t)
    found = vv.passages(track, [_stop(1, 530, at=50), _stop(2, 1150, at=110, final=True)])
    assert _secs(found) == pytest.approx({1: 53.0, 2: 115.0})


def test_a_track_beyond_the_passage_radius_passes_no_stop():
    track = _line(0, 120, 20, lambda t: 10 * t)
    near = vv.PASSAGE_RADIUS_M - 5
    far = vv.PASSAGE_RADIUS_M + 5
    found = vv.passages(track, [_stop(1, 300, near, at=30), _stop(2, 700, far, at=70, final=True)])
    assert set(found) == {1}


def test_a_dwell_departs_when_the_bus_leaves_not_at_its_last_fix_at_the_stop():
    """Stands at the stop at 400 m from 40 s to 67 s, then runs on at 10 m/s.
    The last fix at the stop is at 60 s; the bus is next seen 130 m on."""

    def east(t: int) -> float:
        if t < 40:
            return 10 * t
        if t <= 67:
            return 400
        return 400 + 10 * (t - 67)

    track = _line(0, 160, 20, east)
    found = vv.passages(track, [_stop(1, 400, at=60), _stop(2, 1300, at=150, final=True)])
    assert _secs(found)[1] == pytest.approx(67.0)


def test_a_bus_idling_at_the_terminus_departs_when_it_pulls_out():
    """Fixes at the first stop for ten minutes before it leaves at 605 s."""

    def east(t: int) -> float:
        return 0 if t <= 605 else 8 * (t - 605)

    track = _line(0, 800, 20, east)
    found = vv.passages(track, [_stop(1, 0, at=600), _stop(2, 1200, at=760, final=True)])
    assert _secs(found)[1] == pytest.approx(605.0)


def test_the_last_stop_is_passed_on_arrival_and_not_after_the_layover():
    """Arrives at the terminal, 1000 m on, at 105 s and stands there."""

    def east(t: int) -> float:
        return min(10 * (t - 5), 1000) if t >= 5 else 0

    track = _line(0, 600, 20, east)
    found = vv.passages(track, [_stop(1, 0, at=0), _stop(2, 1000, at=100, final=True)])
    assert _secs(found) == pytest.approx({1: 5.0, 2: 105.0})


def test_a_track_ending_short_of_the_last_stop_has_not_been_seen_arriving():
    """The fixes end 30 m before the terminal, inside the passage radius but
    still moving towards it: no arrival. With one more fix inside the dwell
    tolerance of it, the arrival is timed."""
    stops = [_stop(1, 0, at=0), _stop(2, 1000, at=100, final=True)]
    short = [_fix(t, 10 * t) for t in range(0, 91, 10)] + [_fix(97, 970)]
    assert set(vv.passages(short, stops)) == {1}
    close = [*short, _fix(99, 990)]
    assert _secs(vv.passages(close, stops))[2] == pytest.approx(99.0)


def test_a_loop_passes_its_start_on_leaving_and_its_end_on_returning():
    """Out 1000 m east and back to the same terminal: stop 1 and stop 3 share
    a place. The bus leaves at 0 s, turns at 100 s and returns at 200 s."""

    def east(t: int) -> float:
        return 10 * t if t <= 100 else 1000 - 10 * (t - 100)

    track = [_fix(t, east(min(t, 200))) for t in range(0, 401, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 1000, 5, at=100), _stop(3, 0, at=200, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 100.0, 3: 200.0})


def test_a_loop_idling_long_at_its_terminus_departs_when_it_pulls_out():
    """Twenty minutes at the terminal, which is also the loop's last stop,
    before leaving at 0 s. A return to the same place says nothing about
    whether the bus has left, so it does not close the first stop's window."""
    idle = [_fix(t, 0) for t in range(-1200, 1, 20)]

    def east(t: int) -> float:
        return 10 * t if t <= 100 else 1000 - 10 * (t - 100)

    track = idle + [_fix(t, east(t)) for t in range(20, 201, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 1000, 5, at=100), _stop(3, 0, at=200, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 100.0, 3: 200.0})


def test_a_stop_is_passed_only_after_the_stop_before_it():
    """Stop 3 stands beside the road the bus takes out to stop 2, which it
    passes at 30 s on the way; the bus serves stop 3 on the way back, at 170 s."""

    def east(t: int) -> float:
        return 10 * t if t <= 100 else 1000 - 10 * (t - 100)

    track = [_fix(t, east(t)) for t in range(0, 201, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 1000, at=100), _stop(3, 300, -10, at=170, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 100.0, 3: 170.0})


def test_two_stops_between_the_same_two_fixes_are_both_passed_in_order():
    track = _line(0, 100, 20, lambda t: 10 * t)
    stops = [_stop(1, 0, at=0), _stop(2, 230, at=23), _stop(3, 270, at=27), _stop(4, 1000, at=100, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 23.0, 3: 27.0, 4: 100.0})


def test_a_stop_mapped_behind_the_one_before_it_is_not_passed_before_it():
    """Stop 3's coordinates lie 40 m behind stop 2's along the road, as a pole
    mapped on the wrong side of a junction can. It is passed no earlier than
    stop 2, where the track is still within the radius of it."""
    track = _line(0, 100, 20, lambda t: 10 * t)
    stops = [_stop(1, 0, at=0), _stop(2, 270, at=27), _stop(3, 230, at=28), _stop(4, 1000, at=100, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 27.0, 3: 27.0, 4: 100.0})


def _lasso(t: int) -> tuple[float, float]:
    """Out 1000 m east, 200 m north, back west and south to the start at
    240 s, then on west: 10 m/s throughout."""
    if t <= 100:
        return 10 * t, 0
    if t <= 120:
        return 1000, 10 * (t - 100)
    if t <= 220:
        return 1000 - 10 * (t - 120), 200
    if t <= 240:
        return 0, 200 - 10 * (t - 220)
    return -10 * (t - 240), 0


def test_a_track_starting_after_the_first_stop_does_not_take_the_loops_return_for_it():
    """The trip's positions begin at 20 s, 200 m out: the first stop was left
    unseen. The bus comes back past it at 240 s as the loop's last stop."""
    track = [_fix(t, *_lasso(t)) for t in range(20, 301, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 1000, 100, at=110), _stop(3, 0, at=240, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({2: 110.0, 3: 240.0})


def test_a_figure_eight_starting_late_does_not_take_its_crossing_for_the_first_stop():
    track = [_fix(t, *_lasso(t)) for t in range(20, 301, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 1000, 100, at=110), _stop(3, 0, at=240), _stop(4, -600, at=300, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({2: 110.0, 3: 240.0, 4: 300.0})


def test_a_bus_passing_a_stop_without_stopping_is_timed_along_the_segment_whatever_it_does_next():
    """At 10 m/s through the stop at 10 s, then slowing to 4 m/s. Timing the
    stretch after the stop at the slower speed would put the passage at the
    fix before it."""
    track = [_fix(0, -100), _fix(20, 100), _fix(40, 180), _fix(60, 260), _fix(80, 1000)]
    assert _secs(vv.passages(track, [_stop(1, 0, at=10), _stop(2, 1000, at=80, final=True)]))[1] == pytest.approx(10.0)


def test_a_bus_reaching_the_last_stop_without_standing_there_is_timed_along_the_segment():
    """Crawling at 1.5 m/s, then 10 m/s past the terminal at 30 s; timing the
    approach at the crawl would put the arrival at the fix after it."""
    track = [_fix(0, 0), _fix(20, 30), _fix(40, 230), _fix(60, 430)]
    assert _secs(vv.passages(track, [_stop(1, -500, at=0), _stop(2, 130, at=30, final=True)])) == pytest.approx(
        {2: 30.0}
    )


_FOUR = [_stop(1, 0, at=0), _stop(2, 300, at=30), _stop(3, 600, at=60), _stop(4, 900, at=90, final=True)]


def _deadhead_then_trip(dead_start: int) -> list[vv.Fix]:
    """Running empty back along the route, on the far side of the road, from
    beyond stop 4 to the terminal, standing there, then the trip at 10 m/s
    from 0 s."""
    dead = [_fix(t, 1200 - 10 * (t - dead_start), -10) for t in range(dead_start, dead_start + 121, 20)]
    idle = [_fix(t, 0, -5) for t in range(dead_start + 140, 1, 20)]
    return dead + idle + [_fix(t, 10 * t) for t in range(20, 121, 20)]


@pytest.mark.parametrize("dead_start", [-300, -2400])
def test_a_deadhead_past_the_stops_before_the_trip_is_not_taken_for_it(dead_start):
    """Five and forty minutes before departure the bus passed stops 4, 3 and 2
    in reverse; taking those passes would forfeit the stops between."""
    assert _secs(vv.passages(_deadhead_then_trip(dead_start), _FOUR)) == pytest.approx(
        {1: 0.0, 2: 30.0, 3: 60.0, 4: 90.0}, abs=0.05
    )


def _out_and_back(t: int) -> float:
    return 10 * t if t <= 100 else 1000 - 10 * (t - 100)


_OUT_AND_BACK = [
    _stop(1, 0, at=0),
    _stop(2, 500, at=50),
    _stop(3, 1000, at=100),
    _stop(4, 150, 15, at=185),
    _stop(5, 0, 15, at=200, final=True),
]


def test_an_out_and_back_serves_the_stop_across_the_road_on_the_way_back():
    track = [_fix(t, _out_and_back(t)) for t in range(0, 221, 20)]
    assert _secs(vv.passages(track, _OUT_AND_BACK)) == pytest.approx({1: 0.0, 2: 50.0, 3: 100.0, 4: 185.0, 5: 200.0})


def test_a_late_start_states_why_each_stop_it_cannot_time_has_no_passage():
    """The track starts 100 m out, so the first stop is seen only on the way
    back, after stops it must precede; the turn at 1000 m falls between two
    fixes that stand 100 m short of it."""
    track = [_fix(t, _out_and_back(t)) for t in range(10, 221, 20)]
    found, why = vv.passages_explained(track, _OUT_AND_BACK)
    assert _secs(found) == pytest.approx({2: 50.0, 4: 185.0, 5: 200.0})
    assert why == {1: "out_of_order", 3: "no_visit"}


def test_a_stop_seen_only_hours_off_schedule_or_never_seen_leaving_states_why():
    track = [_fix(t, 10 * t) for t in range(0, 101, 20)] + [_fix(9000 + t, 1500 + 10 * t) for t in range(0, 41, 20)]
    track += [_fix(9060, 1900), _fix(9080, 1900)]
    stops = [_stop(1, 0, at=0), _stop(2, 500, at=50), _stop(3, 1600, at=160), _stop(4, 1900, at=190)]
    found, why = vv.passages_explained(track, stops)
    assert set(found) == {1, 2}
    assert why == {3: "implausible", 4: "unseen"}


def test_of_two_passes_of_a_stop_the_one_that_keeps_the_delay_steady_is_taken():
    """The bus passes stop 2's place at 30 s on its way into a spur and serves
    it at 90 s on the way out, on time; both sequences match all three stops."""

    def at(t: int) -> tuple[float, float]:
        if t <= 30:
            return 10 * t, 0
        if t <= 60:
            return 300, 10 * (t - 30)
        if t <= 90:
            return 300, 300 - 10 * (t - 60)
        return 300 + 10 * (t - 90), 0

    track = [_fix(t, *at(t)) for t in range(0, 151, 10)]
    stops = [_stop(1, 0, at=0), _stop(2, 300, at=90), _stop(3, 900, at=150, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 90.0, 3: 150.0})


def test_a_circulator_falling_behind_lap_by_lap_keeps_each_lap_on_its_own_pass():
    """One trip_id runs a 1 km out-and-back loop twice, laps scheduled 30
    minutes apart. The bus runs 9 minutes late on the first lap and 25 on the
    second: the delay grows by more than a neighbouring-stop change across
    the long gap between the laps, which the second lap's own passes, not the
    first stops matched to later passes, account for. The first lap's last
    stop, where its fixes stop, is never seen left."""
    stops = [
        _stop(1, 0, at=0),
        _stop(2, 1000, at=100),
        _stop(3, 0, at=200),
        _stop(4, 1000, at=1800 + 100),
        _stop(5, 0, at=1800 + 200, final=True),
    ]

    def lap(start: int) -> list[vv.Fix]:
        return [_fix(start + t, 10 * t if t <= 100 else 2000 - 10 * t) for t in range(0, 201, 20)]

    track = lap(540) + lap(1800 + 1500)[1:]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 540.0, 2: 640.0, 4: 3400.0, 5: 3500.0})


def test_one_pass_by_a_place_two_stops_share_is_the_passage_of_only_one():
    """A loop's first and last stops share a place, and the track passes it
    once, without stopping, at 240 s: the last stop's schedule fits it, the
    first stop's does not, and one pass cannot be both."""
    track = [_fix(t, 10 * (t - 240)) for t in range(200, 281, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 0, at=240, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({2: 240.0})


def test_a_stop_the_track_misses_is_not_matched_to_a_later_lap():
    """The fixes stop at 180 s and resume at 600 s, so no usable segment comes
    near stop 3 at 1900 m. The track returns past that place 50 minutes on.
    Stop 3 has no passage, put down to the gap the bus crossed it in rather
    than to the later lap, and stop 4 is still matched."""
    out = [_fix(t, 10 * t) for t in range(0, 181, 20)]
    resumed = [_fix(t, 10 * (t - 400)) for t in range(600, 801, 20)]
    later_lap = [_fix(3000 + t, 1700 + 10 * t) for t in range(0, 41, 20)]
    track = out + resumed + later_lap
    stops = [
        _stop(1, 0, at=0),
        _stop(2, 1000, at=100),
        _stop(3, 1900, at=190),
        _stop(4, 3400, at=740, final=True),
    ]
    found, why = vv.passages_explained(track, stops)
    assert _secs(found) == pytest.approx({1: 0.0, 2: 100.0, 4: 740.0})
    assert why == {3: "gap"}


def test_a_gap_between_the_neighbours_outranks_a_later_lap_too_late_to_count():
    """As above, but the later lap passes stop 3 over two hours behind its
    schedule, so stop 3's own visits are all implausible. The bus still
    crossed it in the gap between stops 2 and 4, and that is the reason."""
    out = [_fix(t, 10 * t) for t in range(0, 181, 20)]
    resumed = [_fix(t, 10 * (t - 400)) for t in range(600, 801, 20)]
    later_lap = [_fix(8000 + t, 1700 + 10 * t) for t in range(0, 41, 20)]
    stops = [
        _stop(1, 0, at=0),
        _stop(2, 1000, at=100),
        _stop(3, 1900, at=190),
        _stop(4, 3400, at=740, final=True),
    ]
    found, why = vv.passages_explained(out + resumed + later_lap, stops)
    assert _secs(found) == pytest.approx({1: 0.0, 2: 100.0, 4: 740.0})
    assert why == {3: "gap"}


def test_a_gap_over_a_stop_after_the_next_stop_was_passed_is_not_the_reason():
    """The bus detours 300 m north of stop 3, rejoins at stop 4, and only
    afterwards loses its fixes across a stretch that runs back over stop 3.
    That gap falls after stop 4's passage, so stop 3 was never visited."""
    track = [
        *(_fix(t, 10 * t) for t in range(0, 101, 20)),
        *(_fix(t, 1200 + 10 * (t - 120), 300) for t in range(120, 271, 30)),
        _fix(290, 3000),
        _fix(310, 3200),
        _fix(700, 1700),
        _fix(720, 1500),
    ]
    stops = [
        _stop(1, 0, at=0),
        _stop(2, 1000, at=100),
        _stop(3, 1900, at=190),
        _stop(4, 3000, at=290, final=True),
    ]
    found, why = vv.passages_explained(track, stops)
    assert set(found) == {1, 2, 4}
    assert why == {3: "no_visit"}


def test_a_gap_ending_at_the_stop_before_s_passage_is_not_the_reason():
    """The fixes resume at 300 s with the bus at stop 2, so its departure is
    timed on the fix that ends the gap. Stop 3 lies on that gap's line, but
    the gap ends at stop 2's passage and stop 3 comes after it."""
    track = [
        *(_fix(t, 10 * t) for t in range(0, 101, 20)),
        *(_fix(t, 2000 + 10 * (t - 300)) for t in range(300, 501, 20)),
    ]
    stops = [
        _stop(1, 0, at=0),
        _stop(2, 1995, at=300),
        _stop(3, 1500, 30, at=350),
        _stop(4, 3500, at=450, final=True),
    ]
    found, why = vv.passages_explained(track, stops)
    assert _secs(found)[2] == pytest.approx(300.0)
    assert set(found) == {1, 2, 4}
    assert why == {3: "no_visit"}


def test_a_revisit_far_from_the_expected_time_is_no_passage():
    """The bus passes stop 2 at 90 s, on time, and stop 3's place only 20
    minutes late, after a gap in which it ran elsewhere: further from the
    delay at stop 2 than a delay moves between neighbouring stops."""
    late = 100 + vv.MAX_DELAY_CHANGE_SEC + 300
    track = [_fix(t, 10 * t) for t in range(0, 101, 20)] + [_fix(late + t, 1900 + 10 * t) for t in range(0, 41, 20)]
    stops = [_stop(1, 0, at=0), _stop(2, 900, at=90), _stop(3, 2000, at=200, final=True)]
    found, why = vv.passages_explained(track, stops)
    assert set(found) == {1, 2}
    assert why == {3: "delay_jump"}


def test_a_bus_held_at_the_second_stop_loses_the_first_to_the_delay_bound():
    """The bus leaves stop 1 on time, then stands at stop 2 for twenty minutes
    and runs the rest of the trip that late. The longer side of the jump is
    taken; stop 1 falls in time before it, so only the delay bound kept it
    out, which is stated apart from a stop out of order."""
    hold = vv.MAX_DELAY_CHANGE_SEC + 300

    def east(t: int) -> float:
        if t <= 30:
            return 10 * t
        return 300 if t <= 30 + hold else 300 + 10 * (t - 30 - hold)

    track = _line(0, 90 + hold, 10, east)
    stops = [_stop(1, 0, at=0), _stop(2, 300, at=30), _stop(3, 600, at=60), _stop(4, 900, at=90, final=True)]
    found, why = vv.passages_explained(track, stops)
    assert _secs(found) == pytest.approx({2: 30.0 + hold, 3: 60.0 + hold, 4: 90.0 + hold})
    assert why == {1: "delay_jump"}


def test_no_passage_is_interpolated_across_a_gap_in_the_fixes():
    gap = vv.MAX_BRACKET_GAP_SEC + 20
    track = [_fix(0, 0), _fix(20, 200), _fix(20 + gap, 200 + 10 * gap), _fix(40 + gap, 400 + 10 * gap)]
    stops = [_stop(1, 0, at=0), _stop(2, 600, at=60), _stop(3, 400 + 10 * gap, at=40 + gap, final=True)]
    found, why = vv.passages_explained(track, stops)
    assert set(found) == {1, 3}
    assert why == {2: "gap"}


def test_a_passage_is_interpolated_across_a_gap_as_long_as_the_limit():
    gap = vv.MAX_BRACKET_GAP_SEC
    track = [_fix(0, 0), _fix(20, 200), _fix(20 + gap, 200 + 10 * gap), _fix(40 + gap, 400 + 10 * gap)]
    stops = [_stop(1, 0, at=0), _stop(2, 600, at=60), _stop(3, 400 + 10 * gap, at=40 + gap, final=True)]
    assert _secs(vv.passages(track, stops)) == pytest.approx({1: 0.0, 2: 60.0, 3: 40.0 + gap})


def test_a_bus_last_seen_standing_at_a_stop_has_not_been_seen_leaving_it():
    track = [_fix(0, 0), _fix(20, 200), _fix(40, 400), _fix(60, 400)]
    stops = [_stop(1, 0, at=0), _stop(2, 400, at=40), _stop(3, 900, at=90, final=True)]
    assert set(vv.passages(track, stops)) == {1}


def test_tracks_drop_resent_fixes_and_gps_jumps_and_split_a_trip_id_by_day():
    """The same trip_id runs on two service days. A stale fix resent by the
    next poll is kept once, and a fix 5 km off for 20 s is dropped."""
    day1, day2 = date(2026, 11, 20), date(2026, 11, 21)
    first_departure = 9 * 3600  # 09:00 JST
    d1 = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    d2 = d1 + timedelta(days=1)

    def s(at: datetime, east: float) -> vv.Sighting:
        return vv.Sighting("T1", "V1", None, at, *_ll(east, 0))

    sightings = [
        s(d1, 0),
        s(d1, 0),
        s(d1 + timedelta(seconds=20), 200),
        s(d1 + timedelta(seconds=40), 5000),
        s(d1 + timedelta(seconds=60), 600),
        s(d2, 0),
        s(d2 + timedelta(seconds=20), 200),
        s(d2 + timedelta(seconds=200), 1000),
    ]
    tracks, stats = vv.trip_tracks(sightings, {"T1": first_departure})
    assert sorted(tracks) == [("T1", day1), ("T1", day2)]
    assert [round(f.at.timestamp() - d1.timestamp()) for f in tracks[("T1", day1)]] == [0, 20, 60]
    assert len(tracks[("T1", day2)]) == 3
    assert stats["gps_jumps_dropped"] == 1
    assert (stats["fix_gaps"], stats["fix_gaps_over_bracket"]) == (4, 1)


def test_a_wild_first_fix_is_dropped_rather_than_every_true_fix_after_it():
    at = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    sightings = [vv.Sighting("T1", "V1", None, at, *_ll(5000, 0))]
    sightings += [vv.Sighting("T1", "V1", None, at + timedelta(seconds=20 * i), *_ll(200 * i, 0)) for i in (1, 2, 3)]
    tracks, stats = vv.trip_tracks(sightings, {"T1": 9 * 3600})
    assert [round((f.at - at).total_seconds()) for f in tracks[("T1", date(2026, 11, 20))]] == [20, 40, 60]
    assert stats["gps_jumps_dropped"] == 1


def test_a_wild_second_fix_does_not_drop_the_true_first_one():
    at = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    east = [0, 5000, 400, 600]
    sightings = [vv.Sighting("T1", "V1", None, at + timedelta(seconds=20 * i), *_ll(e, 0)) for i, e in enumerate(east)]
    tracks, stats = vv.trip_tracks(sightings, {"T1": 9 * 3600})
    assert [round((f.at - at).total_seconds()) for f in tracks[("T1", date(2026, 11, 20))]] == [0, 40, 60]
    assert stats["gps_jumps_dropped"] == 1


def test_a_trip_day_reported_by_two_vehicles_keeps_the_one_seen_most():
    at = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    sightings = [vv.Sighting("T1", "A", None, at + timedelta(seconds=20 * i), *_ll(10 * i, 0)) for i in range(5)]
    sightings += [vv.Sighting("T1", "B", None, at + timedelta(seconds=20 * i), *_ll(0, 10 * i)) for i in range(2)]
    tracks, stats = vv.trip_tracks(sightings, {"T1": 9 * 3600})
    assert len(tracks[("T1", date(2026, 11, 20))]) == 5
    assert stats["trip_days_with_several_tracks"] == 1


def test_vehicle_positions_decode_with_their_own_timestamp_or_else_the_feeds():
    measured = int(T0.timestamp()) - 15
    feed = vehicle_position_feed(
        vehicle_position_entity("T1", vehicle_descriptor("V1"), position=(34.5, 132.5), timestamp=measured),
        vehicle_position_entity("T2", vehicle_descriptor("V2"), position=(34.25, 132.75), start_date="20261120"),
        vehicle_position_entity("T3", vehicle_descriptor("V3")),
        timestamp=int(T0.timestamp()),
    )
    found = vv.decode_vehicle_positions(feed, T0 + timedelta(seconds=3))
    assert [(s.trip_id, s.vehicle_id, s.start_date) for s in found] == [("T1", "V1", None), ("T2", "V2", "20261120")]
    assert found[0].at == T0 - timedelta(seconds=15)
    assert found[1].at == T0
    assert (found[0].lat, found[0].lon) == pytest.approx((34.5, 132.5), abs=1e-5)


def test_an_archive_member_is_timed_by_its_utc_name():
    assert vv.member_instant("20261119/VehiclePosition_235940.pb", "VehiclePosition") == datetime(
        2026, 11, 19, 23, 59, 40, tzinfo=UTC
    )
    assert vv.member_instant("20261119/TripUpdate_235940.pb", "VehiclePosition") is None
    assert vv.member_instant("VehiclePosition_235940.pb", "VehiclePosition") is None


def _clock(sec: int | None) -> str | None:
    return None if sec is None else f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"


def _timetable(*trips: tuple[str, Sequence[tuple[int, str, int | None]]]) -> vv.Timetable:
    stops = {f"S{i}": _ll(100 * i, 0) for i in range(1, 6)}
    return vv.Timetable(
        stops=stops,
        trips={
            trip_id: tuple(vv.StopTime(seq, stop_id, _clock(sec), None) for seq, stop_id, sec in rows)
            for trip_id, rows in trips
        },
    )


def _tu_poll(at: datetime, *updates: bytes) -> tuple[str, datetime, bytes]:
    name = f"{at:%Y%m%d}/TripUpdate_{at:%H%M%S}.pb"
    return name, at, trip_update_feed(*updates, timestamp=int(at.timestamp()))


def test_the_kept_reading_is_the_latest_timely_plausible_one_as_the_app_keeps():
    """09:10 JST departure at stop 1. Readings at 09:00 (60 s), 09:05 (120 s),
    09:07 (an implausible 9000 s, ignored) and, for stop 2, 09:05 then 09:06
    with SKIPPED, which withdraws the stop. A reading 3 h after the departure
    is no reading of it."""
    day = date(2026, 11, 20)
    table = _timetable(("T1", [(1, "S1", 9 * 3600 + 600), (2, "S2", 9 * 3600 + 900)]))
    readings = [
        _tu_poll(T0, trip_update("T1", stop_time_update(1, departure_delay=60))),
        _tu_poll(
            T0 + timedelta(minutes=5),
            trip_update("T1", stop_time_update(1, departure_delay=120), stop_time_update(2, departure_delay=30)),
        ),
        _tu_poll(T0 + timedelta(minutes=7), trip_update("T1", stop_time_update(1, departure_delay=9000))),
        _tu_poll(T0 + timedelta(minutes=6), trip_update("T1", stop_time_update(2, schedule_relationship=1))),
        _tu_poll(T0 + timedelta(hours=3), trip_update("T1", stop_time_update(1, departure_delay=999))),
    ]
    kept = vv.kept_readings(readings, table, day)
    assert set(kept) == {("T1", 1), ("T1", 2)}
    assert (kept[("T1", 1)].dep_delay, kept[("T1", 1)].captured_at) == (120, T0 + timedelta(minutes=5))
    assert kept[("T1", 2)].dep_delay is None


def test_a_tie_on_capture_breaks_on_the_file_name_and_other_days_are_left_out():
    day = date(2026, 11, 20)
    table = _timetable(("T1", [(1, "S1", 9 * 3600 + 600)]))
    same = T0 + timedelta(minutes=1)
    feed_a = trip_update_feed(trip_update("T1", stop_time_update(1, departure_delay=10)))
    feed_b = trip_update_feed(trip_update("T1", stop_time_update(1, departure_delay=20)))
    readings = [("20261120/TripUpdate_000100a.pb", same, feed_a), ("20261120/TripUpdate_000100b.pb", same, feed_b)]
    for ordered in (readings, readings[::-1]):
        assert vv.kept_readings(ordered, table, day)[("T1", 1)].dep_delay == 20
    assert vv.kept_readings(readings, table, day + timedelta(days=1)) == {}


def test_a_cancellation_listing_no_stop_withdraws_the_stops_still_to_come_as_ingest_stores_it():
    """Predicted at 09:00 for 09:02, 09:10 and 09:15; at 09:05 the trip is
    reported CANCELED with no stop. Stop 1 was due before the cancellation and
    keeps its reading; stops 2 and 3 are withdrawn. A stop_sequence no stored
    row can hold is skipped, as ingest skips it. A CANCELED report that lists
    its stops withdraws exactly those."""
    day = date(2026, 11, 20)
    table = _timetable(
        (
            "T1",
            [
                (1, "S1", 9 * 3600 + 120),
                (2, "S2", 9 * 3600 + 600),
                (3, "S3", 9 * 3600 + 900),
                (70000, "S4", 9 * 3600 + 960),
            ],
        )
    )
    predicted = _tu_poll(T0, trip_update("T1", *(stop_time_update(i, departure_delay=60) for i in (1, 2, 3))))
    stopless = _tu_poll(T0 + timedelta(minutes=5), trip_update("T1", schedule_relationship=3))
    kept = vv.kept_readings([predicted, stopless], table, day)
    assert {k: r.dep_delay for k, r in kept.items()} == {("T1", 1): 60, ("T1", 2): None, ("T1", 3): None}
    assert kept[("T1", 2)].captured_at == T0 + timedelta(minutes=5)
    listing = _tu_poll(
        T0 + timedelta(minutes=5), trip_update("T1", stop_time_update(3, departure_delay=60), schedule_relationship=3)
    )
    kept = vv.kept_readings([predicted, listing], table, day)
    assert {k: r.dep_delay for k, r in kept.items()} == {("T1", 1): 60, ("T1", 2): 60, ("T1", 3): None}


def test_a_reading_before_the_cutover_files_under_the_next_service_date():
    """Captured at 03:55 JST on 11-20, still service date 11-19, for a 04:05
    departure: the reading is 11-20's, and a run over 11-20 alone keeps it."""
    table = _timetable(("T1", [(1, "S1", 4 * 3600 + 300)]))
    captured = datetime(2026, 11, 19, 18, 55, tzinfo=UTC)
    poll = _tu_poll(captured, trip_update("T1", stop_time_update(1, departure_delay=30)))
    assert vv.kept_readings([poll], table, date(2026, 11, 20))[("T1", 1)].dep_delay == 30
    assert vv.kept_readings([poll], table, date(2026, 11, 19)) == {}
    held: dict = {}
    vv.merge_readings(held, [poll], lambda _day: table, date(2026, 11, 19), date(2026, 11, 19))
    assert held == {}


def _gtfs_zip(path: Path, stop_times: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nA,A,34.1,132.1\nB,B,34.2,132.2\nC,C,,\n")
        zf.writestr("stop_times.txt", stop_times)
    return path


def test_the_timetable_reads_timepoints_only_where_the_feed_states_them(tmp_path):
    stated = vv.read_timetable(
        _gtfs_zip(
            tmp_path / "a.zip",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
            "T,07:00:00,07:00:00,A,1,1\nT,,,B,2,0\nT,25:10:00,25:10:00,C,3,\n",
        )
    )
    assert stated.trips["T"] == (
        vv.StopTime(1, "A", "07:00:00", True),
        vv.StopTime(2, "B", None, False),
        vv.StopTime(3, "C", "25:10:00", True),
    )
    assert set(stated.stops) == {"A", "B"}
    unstated = vv.read_timetable(
        _gtfs_zip(
            tmp_path / "b.zip",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT,07:00:00,07:00:00,A,1\n",
        )
    )
    assert [st.scheduled_sec for st in stated.trips["T"]] == [7 * 3600, None, 25 * 3600 + 600]
    assert unstated.trips["T"] == (vv.StopTime(1, "A", "07:00:00", None),)


def _event(error: float, lead: float, sec: int = 9 * 3600, timepoint: bool | None = None) -> vv.Event:
    return vv.Event(
        "T", date(2026, 11, 20), 1, sec, timepoint, predicted_delay_sec=0, observed_delay_sec=-error, lead_sec=lead
    )


def test_figures_are_the_mae_and_bias_by_lead_hour_and_timepoint():
    events = [
        _event(30, 10, timepoint=True),
        _event(-10, 45, timepoint=False),
        _event(20, -5, sec=10 * 3600 + 5),
        _event(100, vv.LEAD_CAP_SEC + 900, sec=25 * 3600),
    ]
    figures = vv.figures(events)
    assert figures["overall"] == {"n": 4, "mae_sec": 40.0, "bias_sec": 35.0, "p90_abs_error_sec": 100.0}
    by_lead = {(g["lead_from_sec"], g["lead_to_sec"]): g["n"] for g in figures["by_lead"]}
    assert by_lead == {(-vv.LEAD_WIDTH_SEC, 0): 1, (0, vv.LEAD_WIDTH_SEC): 2, (vv.LEAD_CAP_SEC, None): 1}
    assert [(g["hour"], g["n"], g["bias_sec"]) for g in figures["by_hour"]] == [
        (9, 2, 10.0),
        (10, 1, 20.0),
        (25, 1, 100.0),
    ]
    assert [(g["timepoint"], g["n"]) for g in figures["by_timepoint"]] == [("no", 1), ("unknown", 2), ("yes", 1)]


def test_figures_over_no_event_state_none_rather_than_divide():
    figures = vv.figures([])
    assert figures["overall"] == {"n": 0, "mae_sec": None, "bias_sec": None, "p90_abs_error_sec": None}
    assert figures["on_time"]["agreement_pct"] is None
    assert set(figures["on_time"]["kept_pct"].values()) == {None}


def test_the_on_time_classes_hold_their_bounds_on_time_as_the_app_counts_them():
    assert vv.ON_TIME_WINDOW == (60, 300)
    early, late = 60, 300
    classes = [vv.on_time_class(d) for d in (-early - 0.5, -early, 0, late, late + 0.5)]
    assert classes == ["early", "on_time", "on_time", "on_time", "late"]


def _classed(predicted: int, observed: float) -> vv.Event:
    return vv.Event("T", date(2026, 11, 20), 1, 9 * 3600, None, predicted, observed, 0)


def test_on_time_agreement_shows_each_direction_a_bias_moves_events():
    """The kept delay reads half a minute early of the observed one: an event
    observed just late is kept on time, and one kept just early is observed
    on time. The net on-time share alone would hide both moves."""
    events = [
        _classed(290, 320),
        _classed(-70, -40),
        _classed(100, 130),
        _classed(100, 130),
        _classed(400, 430),
    ]
    on_time = vv.on_time_agreement(events)
    assert on_time["n"] == 5
    assert on_time["kept_pct"] == {"early": 20.0, "on_time": 60.0, "late": 20.0}
    assert on_time["observed_pct"] == {"early": 0.0, "on_time": 60.0, "late": 40.0}
    assert on_time["agreement_pct"] == 60.0
    assert on_time["kept_to_observed"] == {
        "early": {"early": 0, "on_time": 1, "late": 0},
        "on_time": {"early": 0, "on_time": 2, "late": 1},
        "late": {"early": 0, "on_time": 0, "late": 1},
    }


def _tar(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _archive(scenario: Scenario, root: Path, agency_id: int, vp_interval_sec: int) -> None:
    """Write the scenario as R2 holds it: one tar per UTC day of TripUpdate
    and of VehiclePosition polls, and the timetable zip dated its first day."""
    by_day: dict[tuple[str, str], dict[str, bytes]] = {}
    for p in polls(scenario):
        by_day.setdefault(("rt", p.source_file[:8]), {})[p.source_file] = p.payload
    for vp in vehicle_positions(scenario, vp_interval_sec):
        by_day.setdefault(("vp", vp.source_file[:8]), {})[vp.source_file] = vp.payload
    for (kind, day), members in by_day.items():
        _tar(root / kind / str(agency_id) / f"{day}.tar.gz", members)
    static = root / "static" / str(agency_id) / f"gtfs_static_{scenario.days[0].date:%Y%m%d}.zip"
    static.parent.mkdir(parents=True, exist_ok=True)
    static.write_bytes(static_gtfs_zip(scenario))


DAY = date(2026, 6, 1)


def _on_grid() -> Scenario:
    """Two runs on a five-stop line whose feed propagates the current delay.
    Every departure falls on the 20 s sampling grid, so the reconstructed
    passages are the truth itself."""
    stops = tuple(
        Stop(f"G{i}", f"Grid {i}", round(34.67 + i * 0.002, 6), round(132.45 + i * 0.002, 6)) for i in range(1, 6)
    )
    route = Route("G", "Grid line", stops)
    a = ScheduledTrip("G_0700", "G", "平日", ("07:00:00", "07:02:00", "07:04:00", "07:06:00", "07:08:00"))
    b = ScheduledTrip("G_0730", "G", "平日", ("07:30:00", "07:32:00", "07:34:00", "07:36:00", "07:38:00"))
    return Scenario(
        name="on_grid",
        routes=(route,),
        timetable=(a, b),
        days=(ServiceDay(DAY, "平日"),),
        runs=(
            TripRun.from_delays(a, DAY, (0, 40, 100, 60, 120)),
            TripRun.from_delays(b, DAY, (-60, -20, 0, 20, 20)),
        ),
        feed=FeedModel(prediction="propagate_current_delay", poll_interval_sec=20, lead_sec=300),
    )


def _run(root: Path, out: Path, agency_id: int, first: date, last: date, *, exit_code: int = 0) -> dict:
    days = ["--from", first.isoformat(), "--to", last.isoformat()]
    code = vv.main(["--agency", str(agency_id), *days, "--archives", str(root), "--out", str(out)])
    assert code == exit_code
    return json.loads((out / str(agency_id) / f"{first:%Y%m%d}_{last:%Y%m%d}.json").read_text())


def _oracle_lead_groups(found: oracle.Outcome) -> dict[int, oracle.PredictionError]:
    return oracle.prediction_error_by_lead_time(found, vv.LEAD_WIDTH_SEC)


def test_on_the_sampling_grid_the_figures_are_the_oracles(tmp_path):
    scenario = _on_grid()
    _archive(scenario, tmp_path / "r2", 8, 20)
    summary = _run(tmp_path / "r2", tmp_path / "out", 8, DAY, DAY)
    found = oracle.outcome(scenario)
    expected = oracle.prediction_error(found.predictions)
    assert summary["coverage"]["stop_events"] == expected.departures == 10
    assert summary["coverage"]["with_passage"] == 10
    overall = summary["figures"]["overall"]
    assert overall["n"] == expected.departures
    assert overall["mae_sec"] == pytest.approx(float(expected.mae_sec), abs=0.05)
    assert overall["bias_sec"] == pytest.approx(float(expected.bias_sec), abs=0.05)
    assert expected.mae_sec != 0
    by_lead = {g["lead_from_sec"]: (g["n"], g["mae_sec"], g["bias_sec"]) for g in summary["figures"]["by_lead"]}
    assert by_lead == {
        start: (e.departures, pytest.approx(float(e.mae_sec), abs=0.05), pytest.approx(float(e.bias_sec), abs=0.05))
        for start, e in _oracle_lead_groups(found).items()
    }
    on_time = summary["figures"]["on_time"]
    classed = Counter(
        (vv.on_time_class(p.departure.delay_sec), vv.on_time_class(p.true_delay_sec)) for p in found.predictions
    )
    assert on_time["kept_to_observed"] == {
        k: {o: classed[(k, o)] for o in vv.ON_TIME_CLASSES} for k in vv.ON_TIME_CLASSES
    }
    assert summary["thresholds"]["on_time_early_tolerance_sec"] == 60
    markdown = (tmp_path / "out" / "8" / f"{DAY:%Y%m%d}_{DAY:%Y%m%d}.md").read_text()
    assert "| overall | 10 |" in markdown
    assert f"| on_time | {on_time['kept_pct']['on_time']} | {on_time['observed_pct']['on_time']} |" in markdown


def test_off_the_grid_the_figures_stay_within_a_second_of_the_oracles(tmp_path):
    """The runner's imperfect_feed scenario, sampled every 20 s: departures fall
    between samples and the run changes speed at a stop it does not stop at.
    The emitter stops sampling a run when it reaches its terminal, so the
    07:10 and 07:40 runs, arriving 10 s after a sample and still some 24 m
    out at it, are never seen arriving; every other stop event is measured."""
    scenario = imperfect_feed(DAY)
    _archive(scenario, tmp_path / "r2", 9, 20)
    summary = _run(tmp_path / "r2", tmp_path / "out", 9, DAY, DAY)
    found = oracle.outcome(scenario)
    unseen = {("P1_0710", 8), ("P1_0740", 8)}
    measured = [p for p in found.predictions if (p.departure.trip_id, p.departure.stop_sequence) not in unseen]
    expected = oracle.prediction_error(measured)
    coverage = summary["coverage"]
    assert coverage["stop_events"] == len(found.predictions) == 40
    assert (coverage["with_passage"], coverage["no_passage"]) == (expected.departures, 2) == (38, 2)
    assert summary["figures"]["overall"]["mae_sec"] == pytest.approx(float(expected.mae_sec), abs=1.0)
    assert summary["figures"]["overall"]["bias_sec"] == pytest.approx(float(expected.bias_sec), abs=1.0)
    negative = sum(1 for p in measured if p.lead_sec < 0)
    assert negative > 0
    assert coverage["kept_after_passage"] == negative


def test_a_stop_event_with_no_vehicle_is_counted_but_not_measured(tmp_path, capsys):
    """With the VehiclePositions tar gone, each stop event lacks a track, and
    the missing archives are stated beside the figures: the run's 07:00 JST
    polls sit in the UTC day before, so neither the service day's own UTC day
    nor, for TripUpdates, the next has a tar."""
    scenario = _on_grid()
    root = tmp_path / "r2"
    _archive(scenario, root, 8, 20)
    for vp in (root / "vp" / "8").iterdir():
        vp.unlink()
    summary = _run(root, tmp_path / "out", 8, DAY, DAY, exit_code=1)
    assert summary["coverage"]["stop_events"] == 10
    assert summary["coverage"]["with_passage"] == 0
    assert summary["coverage"]["trip_without_track"] == 10
    assert summary["figures"]["overall"]["n"] == 0
    assert summary["days"][0]["missing_archives"] == [
        "rt/8/20260601.tar.gz",
        "rt/8/20260602.tar.gz",
        "vp/8/20260531.tar.gz",
        "vp/8/20260601.tar.gz",
    ]
    assert "Missing archives: 4" in (tmp_path / "out" / "8" / "20260601_20260601.md").read_text()
    assert "4 archives missing" in capsys.readouterr().out


def test_an_archive_two_service_dates_need_is_missing_once(tmp_path):
    summary = {"days": [{"missing_archives": ["rt/8/20260601.tar.gz"]}, {"missing_archives": ["rt/8/20260601.tar.gz"]}]}
    assert vv.missing_archives(summary) == ["rt/8/20260601.tar.gz"]


@pytest.mark.parametrize("header", [None, int(datetime(2026, 11, 19, 16, 0, tzinfo=UTC).timestamp())])
def test_a_poll_stamped_from_its_name_files_under_the_day_before_its_archive(tmp_path, header):
    """A poll of UTC 11-20 01:00 with no usable header is stamped from its
    name read as JST, 01:00 on 11-20, which is service date 11-19: the run
    reads archive 11-20 before measuring 11-19."""
    root = tmp_path / "r2"
    (root / "static" / "9").mkdir(parents=True)
    _gtfs_zip(
        root / "static" / "9" / "gtfs_static_20261101.zip",
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,01:30:00,01:30:00,A,1\n",
    )
    feed = trip_update_feed(trip_update("T1", stop_time_update(1, departure_delay=30)), timestamp=header)
    _tar(root / "rt" / "9" / "20261120.tar.gz", {"20261120/TripUpdate_010000.pb": feed})
    for last in (date(2026, 11, 19), date(2026, 11, 20)):
        coverage = vv.validate(9, date(2026, 11, 19), last, root)["coverage"]
        assert (coverage["stop_events"], coverage["readings_after_measurement"]) == (1, 0)


def test_a_reading_for_a_date_already_measured_is_counted_not_lost(tmp_path, monkeypatch):
    """Archive 06-03 is read after 06-01 was measured; a reading it filed
    under 06-01 is counted rather than dropped."""
    real = vv.merge_readings
    late = vv.Reading(datetime(2026, 11, 20, tzinfo=UTC), "late.pb", 0)

    def merge_then_late(kept, polls, timetable_on, first, last):
        real(kept, polls, timetable_on, first, last)
        kept.setdefault(date(2026, 6, 1), {})[("X", 1)] = late

    root = tmp_path / "r2"
    _archive(_on_grid(), root, 8, 20)
    for day in ("20260602", "20260603"):
        _tar(root / "rt" / "8" / f"{day}.tar.gz", {})
    monkeypatch.setattr(vv, "merge_readings", merge_then_late)
    coverage = vv.validate(8, DAY, DAY + timedelta(days=1), root)["coverage"]
    assert coverage["readings_after_measurement"] == 1


def test_a_run_with_nothing_to_compare_exits_one(tmp_path):
    args = ["--agency", "8", "--from", "2026-06-01", "--to", "2026-06-01", "--archives", str(tmp_path / "r2")]
    assert vv.main([*args, "--out", str(tmp_path / "out")]) == 1
    assert json.loads((tmp_path / "out" / "8" / "20260601_20260601.json").read_text())["days"][0]["timetable"] is None


def test_fetch_downloads_only_the_archives_the_dates_need_and_never_shows_the_endpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "bucket-x")
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://endpoint.invalid")
    listing = {
        "rt/8/": ["rt/8/20260530.tar.gz", "rt/8/20260531.tar.gz", "rt/8/20260601.tar.gz", "rt/8/20260602.tar.gz"],
        "vp/8/": ["vp/8/20260531.tar.gz"],
        "static/8/": ["static/8/gtfs_static_20260401.zip", "static/8/gtfs_static_20260520.zip"],
    }

    def capture(cmd: Sequence[str]) -> str:
        prefix = cmd[3].removeprefix("s3://bucket-x/")
        return "".join(f"2026-06-01 00:00:00 10 {key}\n" for key in listing[prefix])

    fetched: list[str] = []

    def run(cmd: Sequence[str]) -> None:
        key = cmd[3].removeprefix("s3://bucket-x/")
        fetched.append(key)
        if key.startswith("vp/"):
            raise subprocess.CalledProcessError(1, list(cmd))
        Path(cmd[4]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[4]).write_bytes(b"")

    root = tmp_path / "r2"
    (root / "rt" / "8").mkdir(parents=True)
    (root / "rt" / "8" / "20260601.tar.gz").write_bytes(b"")
    vv.fetch_archives(8, date(2026, 6, 1), date(2026, 6, 1), root, run=run, capture=capture)
    assert sorted(fetched) == [
        "rt/8/20260531.tar.gz",
        "rt/8/20260602.tar.gz",
        "static/8/gtfs_static_20260520.zip",
        "vp/8/20260531.tar.gz",
    ]
    err = capsys.readouterr().err
    assert "vp/8/20260531.tar.gz" in err and "exit 1" in err
    assert "endpoint.invalid" not in err


def test_the_download_runner_drops_the_aws_clis_own_output(capfd):
    noisy = [
        sys.executable,
        "-c",
        "import sys; print('https://endpoint.invalid'); sys.stderr.write('endpoint.invalid'); sys.exit(3)",
    ]
    with pytest.raises(subprocess.CalledProcessError) as raised:
        vv.quiet_run(noisy)
    assert raised.value.returncode == 3
    assert capfd.readouterr() == ("", "")


def test_the_lead_buckets_floor_as_the_oracle_groups():
    assert [vv.lead_bucket(x) for x in (-1, 0, 59.9, 60, vv.LEAD_CAP_SEC + 1, -vv.LEAD_CAP_SEC - 1)] == [
        -vv.LEAD_WIDTH_SEC,
        0,
        0,
        vv.LEAD_WIDTH_SEC,
        vv.LEAD_CAP_SEC,
        -vv.LEAD_CAP_SEC,
    ]
    assert Fraction(vv.LEAD_CAP_SEC, vv.LEAD_WIDTH_SEC).denominator == 1
