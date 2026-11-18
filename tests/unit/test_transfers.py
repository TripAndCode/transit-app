"""The transfer-reliability rules (pipeline.reports.transfers) on hand-built
plans and predictions."""

import time
from bisect import bisect_left
from datetime import date, timedelta

import pytest

from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from pipeline.reports.transfers import (
    Connection,
    StopEvent,
    Tally,
    _DayDepartures,
    buffer_needed,
    connections,
    days_of_type,
    delays,
    percentile_cont,
    summarise,
    worst_days,
)
from pipeline.reports.transfers import (
    _connection as match_one,
)

D = date(2026, 10, 7)
WALK = 120


def a(sec: int, trip: str = "a1", day: date = D) -> StopEvent:
    return StopEvent(day, trip, 5, sec)


def b(sec: int, trip: str, day: date = D) -> StopEvent:
    return StopEvent(day, trip, 3, sec)


def k(e: StopEvent):
    return (e.day, e.trip_id, e.stop_sequence)


def one(arrival, departures, arr_delay, dep_delay) -> Connection:
    found, no_onward = connections([arrival], departures, arr_delay, dep_delay, WALK)
    assert no_onward == 0
    (c,) = found
    return c


def test_a_connection_holds_when_b_leaves_after_a_arrives_plus_the_walk():
    arrival, planned = a(36000), b(36300, "b1")
    c = one(arrival, [planned], {k(arrival): 60}, {k(planned): 0})
    assert c.outcome == "held"
    assert c.wait_sec == 36300 - 36060
    assert c.need_sec == 60 - 0 + WALK


def test_a_connection_holds_on_the_exact_second():
    arrival, planned = a(36000), b(36300, "b1")
    c = one(arrival, [planned], {k(arrival): 180}, {k(planned): 0})
    assert c.outcome == "held"


def test_a_missed_connection_waits_for_the_next_departure():
    arrival, planned, later = a(36000), b(36300, "b1"), b(37200, "b2")
    c = one(arrival, [planned, later], {k(arrival): 300}, {k(planned): 0, k(later): 60})
    assert c.outcome == "missed"
    assert c.wait_sec == 37260 - 36300
    assert not c.stranded


def test_the_planned_departure_is_the_first_at_or_after_arrival_plus_walk():
    arrival, too_soon, planned = a(36000), b(36060, "b0"), b(36120, "b1")
    c = one(arrival, [too_soon, planned], {k(arrival): 0}, {k(too_soon): 0, k(planned): -1})
    assert c.outcome == "missed"


@pytest.mark.parametrize("missing", ["a", "b", "both"])
def test_a_connection_with_no_prediction_on_either_side_is_unobserved(missing):
    arrival, planned = a(36000), b(36300, "b1")
    arr = {} if missing in ("a", "both") else {k(arrival): 0}
    dep = {} if missing in ("b", "both") else {k(planned): 0}
    c = one(arrival, [planned], arr, dep)
    assert c.outcome == "unobserved"
    assert c.wait_sec is None and c.need_sec is None
    tally = Tally()
    tally.add(c)
    assert (tally.planned, tally.held, tally.missed, tally.unobserved) == (1, 0, 0, 1)
    assert tally.held_share is None


def test_an_unobserved_trip_before_a_catchable_one_leaves_the_wait_unknown():
    arrival, planned, unseen, seen = a(36000), b(36300, "b1"), b(36900, "b2"), b(37500, "b3")
    c = one(arrival, [planned, unseen, seen], {k(arrival): 600}, {k(planned): 0, k(seen): 0})
    assert c.outcome == "missed"
    assert c.wait_sec is None
    assert not c.stranded


def test_a_missed_connection_with_no_later_departure_that_day_is_stranded():
    arrival, planned = a(36000), b(36300, "b1")
    c = one(arrival, [planned], {k(arrival): 600}, {k(planned): 0})
    assert c.outcome == "missed"
    assert c.stranded and c.wait_sec is None


def test_an_earlier_departure_running_late_enough_is_the_one_caught():
    arrival, late_earlier, planned = a(36000), b(35900, "b0"), b(36300, "b1")
    c = one(arrival, [late_earlier, planned], {k(arrival): 100}, {k(late_earlier): 340, k(planned): 0})
    assert c.outcome == "held"
    assert c.wait_sec == 35900 + 340 - 36100


def test_an_arrival_with_no_later_departure_that_day_plans_no_connection():
    found, no_onward = connections([a(80000)], [b(36000, "b1")], {}, {}, WALK)
    assert found == [] and no_onward == 1


def test_connections_stay_inside_their_service_day():
    after_midnight = a(24 * 3600 + 1200, day=D)  # 24:20 on D's clock
    same_day = b(24 * 3600 + 2100, "late", day=D)
    next_morning = b(5 * 3600, "early", day=date(2026, 10, 8))
    found, _ = connections(
        [after_midnight],
        [next_morning, same_day],
        {k(after_midnight): 0},
        {k(same_day): 0, k(next_morning): 0},
        WALK,
    )
    (c,) = found
    assert (c.day, c.hour, c.outcome, c.wait_sec) == (D, 24, "held", 900)


ROWS = [("a1", D, 5, 30, None), ("a2", D, 5, 30, 10), ("b1", D, 3, None, 50), ("x", date(2026, 1, 1), 1, 5, 5)]


def test_arrival_delay_falls_back_to_departure_and_b_reads_departures_only():
    arriving = delays(ROWS, {D}, arriving=True, arrivals_confirmed=True)
    assert arriving == {(D, "a1", 5): 30, (D, "a2", 5): 10, (D, "b1", 3): 50}
    departing = delays(ROWS, {D}, arriving=False, arrivals_confirmed=True)
    assert departing == {(D, "a1", 5): 30, (D, "a2", 5): 30}


def test_a_departure_standing_in_for_an_arrival_carries_the_scheduled_hold():
    # Scheduled to arrive 10:00 and leave 10:05; it leaves on time, having
    # arrived as late as 10:05, so a 10:04 departure across the hub is missed.
    arrival = StopEvent(D, "a1", 5, 36000, dwell=300)
    planned = b(36240, "b1")
    rows = [("a1", D, 5, 0, None), ("b1", D, 3, 0, None)]
    arr = delays(rows, {D}, arriving=True, arrivals_confirmed=True, dwell={k(arrival): arrival.dwell})
    assert arr[k(arrival)] == 300
    c = one(arrival, [planned], arr, delays(rows, {D}, arriving=False, arrivals_confirmed=True))
    assert c.outcome == "missed"
    assert c.need_sec == 300 + WALK


def test_a_reported_arrival_needs_no_hold():
    arrival = StopEvent(D, "a1", 5, 36000, dwell=300)
    rows = [("a1", D, 5, 0, 60)]
    assert delays(rows, {D}, arriving=True, arrivals_confirmed=True, dwell={k(arrival): 300}) == {k(arrival): 60}


def test_a_withdrawn_planned_departure_is_missed_and_the_rider_waits_for_the_next():
    arrival, cancelled, later = a(36000), b(36300, "b1"), b(37200, "b2")
    found, _ = connections([arrival], [cancelled, later], {k(arrival): 0}, {k(later): 0}, WALK, {k(cancelled)})
    (c,) = found
    assert (c.outcome, c.wait_sec, c.need_sec, c.stranded) == ("missed", 1200, None, False)


def test_a_withdrawn_planned_departure_is_missed_even_when_the_arrival_is_unobserved():
    arrival, cancelled = a(36000), b(36300, "b1")
    found, _ = connections([arrival], [cancelled], {}, {}, WALK, {k(cancelled)})
    (c,) = found
    assert (c.outcome, c.wait_sec) == ("missed", None)


def test_withdrawn_trips_are_never_the_one_caught():
    arrival, late_earlier, planned, cancelled, last = (
        a(36000),
        b(35900, "b0"),
        b(36300, "b1"),
        b(36600, "b2"),
        b(37200, "b3"),
    )
    dep = {k(late_earlier): 1200, k(planned): 0, k(last): 0}
    found, _ = connections(
        [arrival],
        [late_earlier, planned, cancelled, last],
        {k(arrival): 600},
        dep,
        WALK,
        {k(late_earlier), k(cancelled)},
    )
    (c,) = found
    # The late earlier trip and the 10:10 were withdrawn: the 10:20 is the one caught.
    assert (c.outcome, c.wait_sec, c.stranded) == ("missed", 37200 - 36600, False)


def test_arrival_delays_are_ignored_unless_the_feed_is_confirmed_to_send_them():
    assert delays(ROWS, {D}, arriving=True, arrivals_confirmed=False) == {(D, "a1", 5): 30, (D, "a2", 5): 30}


def test_an_earlier_late_trip_saves_a_rider_the_last_planned_trip_would_strand():
    arrival, late_earlier, last = a(36000), b(35900, "b0"), b(36300, "b1")
    c = one(arrival, [late_earlier, last], {k(arrival): 600}, {k(late_earlier): 1200, k(last): 0})
    assert c.outcome == "missed"
    assert not c.stranded
    assert c.wait_sec == 35900 + 1200 - 36600


def test_an_earlier_late_trip_does_not_settle_a_wait_an_unobserved_trip_leaves_open():
    arrival, late_earlier, planned, unseen = a(36000), b(35900, "b0"), b(36300, "b1"), b(36900, "b2")
    c = one(arrival, [late_earlier, planned, unseen], {k(arrival): 600}, {k(late_earlier): 1200, k(planned): 0})
    assert c.outcome == "missed" and c.wait_sec is None and not c.stranded


def test_matching_stays_linear_in_the_departures_of_a_day():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(16)]
    arrivals = [StopEvent(d, f"a{i}", 5, 18000 + i * 45) for d in days for i in range(1600)]
    departures = [StopEvent(d, f"b{i}", 3, 18060 + i * 45) for d in days for i in range(1600)]
    arr = {k(e): (i % 7) * 60 for i, e in enumerate(arrivals)}
    dep = {k(e): (i % 5) * 60 for i, e in enumerate(departures)}
    started = time.perf_counter()
    found, _ = connections(arrivals, departures, arr, dep, WALK)
    elapsed = time.perf_counter() - started
    assert len(found) == len(arrivals) - len(days) * 2
    # A smoke check only: the bound on what each arrival reads is pinned by
    # test_an_arrival_reads_departures_within_the_delay_clamp_only.
    assert elapsed < 10.0


class _Counted(list):
    """A list that counts the elements read from it."""

    reads = 0

    def __getitem__(self, index):
        value = super().__getitem__(index)
        type(self).reads += len(value) if isinstance(index, slice) else 1
        return value


def test_an_arrival_reads_departures_within_the_delay_clamp_only():
    spacing = 45
    events = [StopEvent(D, f"b{i}", 3, 18000 + i * spacing) for i in range(1600)]
    secs = [e.sec for e in events]
    predicted = _Counted(e.sec for e in events)
    day_b = _DayDepartures(events, secs, predicted, [False] * len(events))
    arrival = a(18000 + 800 * spacing)
    planned = bisect_left(secs, arrival.sec + WALK)
    _Counted.reads = 0
    match_one(arrival, day_b, planned, {k(arrival): 0}, WALK)
    window = 2 * MAX_PLAUSIBLE_DELAY_SEC // spacing
    assert _Counted.reads <= window // 2 + 4


def test_the_buffer_holds_at_least_95_percent_of_needs_and_never_undercuts_the_walk():
    needs = [i * 60 for i in range(20)]
    assert buffer_needed(needs, WALK) == 18
    held = sum(n <= 18 * 60 for n in needs)
    assert held / len(needs) >= 0.95
    assert buffer_needed([-300] * 30, WALK) == 2


def test_percentile_cont_interpolates():
    assert percentile_cont([0, 10], 0.5) == 5
    assert percentile_cont([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)


def _connection(outcome, wait=None, need=None, day=D, hour=10) -> Connection:
    return Connection(day, hour, outcome, wait, need, False)


def test_figures_are_withheld_below_the_sample_floor():
    few = [_connection("held", 300, 100)] * (MIN_GROUP_SAMPLES - 1)
    r = summarise(few, 0, WALK, [D], [])
    assert r.total.held == MIN_GROUP_SAMPLES - 1
    assert r.held_share is None and r.buffer_min is None
    assert r.wait_p50_sec is None and r.within_band_share is None
    assert r.by_day[D].small_sample


def test_unobserved_connections_do_not_lift_a_pair_over_the_floor():
    rows = [_connection("held", 300, 100)] * (MIN_GROUP_SAMPLES - 1) + [_connection("unobserved")] * 50
    r = summarise(rows, 0, WALK, [D], [])
    assert r.total.planned == MIN_GROUP_SAMPLES + 49
    assert r.held_share is None


def test_figures_above_the_floor():
    rows = [_connection("held", 240, 60)] * 30 + [_connection("missed", 900, 400)] * 10
    r = summarise(rows, 0, WALK, [D], [date(2026, 10, 8)])
    assert r.held_share == 0.75
    assert r.waits == 40
    assert r.wait_p50_sec == 240
    assert r.within_band_share == {300: 0.75, 600: 0.75, 1200: 1.0}
    assert r.buffer_min == 7
    assert r.no_plan_days == [date(2026, 10, 8)]
    assert list(r.by_hour) == [10]


def test_worst_days_rank_on_missed_count_then_held_share():
    def tally(held, missed):
        t = Tally()
        for _ in range(held):
            t.add(_connection("held"))
        for _ in range(missed):
            t.add(_connection("missed"))
        return t

    days = {date(2026, 10, d): t for d, t in [(1, tally(5, 1)), (2, tally(1, 3)), (3, tally(9, 3)), (4, tally(9, 0))]}
    assert worst_days(days) == [date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 1)]


def test_day_types_follow_the_weekday_rule_with_holidays():
    # 2026-10-12 is Sports Day, a Monday.
    weekdays = days_of_type(date(2026, 10, 10), date(2026, 10, 14), "weekday")
    assert weekdays == [date(2026, 10, 13), date(2026, 10, 14)]
    assert days_of_type(date(2026, 10, 10), date(2026, 10, 14), "sunday_holiday") == [
        date(2026, 10, 11),
        date(2026, 10, 12),
    ]


def test_the_planned_table_leaves_out_a_stop_sequence_updates_cannot_hold():
    import json

    from pipeline.reports.transfers import _planned_table

    table = _planned_table([b(36000, "ok"), StopEvent(D, "neg", -1, 0), StopEvent(D, "big", 65536, 0)])
    rows = [json.loads(line) for line in table.files[0].data.decode().splitlines()]
    assert rows == [{"trip_id": "ok", "stop_sequence": 3}]
