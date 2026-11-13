"""The scenario generator and its oracle, checked by hand without a database."""

import csv
import hashlib
import io
import json
import struct
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest

from pipeline.strategies._pb import _fields, _int32
from pipeline.strategies.static_join import _decode_rows
from tests.fixtures import scenario_oracle as oracle
from tests.fixtures.scenario import (
    CANCELED,
    JST,
    NO_DATA,
    SCHEDULED,
    SKIPPED,
    Cancellation,
    Delivery,
    FeedModel,
    Freeze,
    Misreport,
    Relisting,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    TimetableVersion,
    TripRun,
    Unreported,
    capture_day,
    gtfs_clock,
    gtfs_seconds,
    handoff,
    poll_batches,
    polls,
    service_day_instant,
    static_gtfs_zip,
    vehicle_id,
    vehicle_positions,
    write_vehicle_positions,
)
from tests.pipeline.test_scenarios import imperfect_feed, relisted

DAY = date(2026, 6, 1)
_STOPS = tuple(Stop(f"S{i}", f"Stop {i}", 34.0 + i / 1000, 132.0) for i in range(1, 4))
_ROUTE = Route("R", "Route", _STOPS)
_EVENING = ScheduledTrip("R_2350", "R", "平日", ("23:50:00", "23:58:00", "24:06:00"))
_MORNING = ScheduledTrip("R_0700", "R", "平日", ("07:00:00", "07:08:00", "07:16:00"))


def _scenario(feed: FeedModel | None = None) -> Scenario:
    return Scenario(
        name="tiny",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING),
        days=(ServiceDay(DAY, "平日"),),
        runs=(
            TripRun.from_delays(_MORNING, DAY, (-61, 60, 61)),
            TripRun.from_delays(_EVENING, DAY, (300, 301, 0)),
        ),
        feed=feed or FeedModel(poll_interval_sec=60, lead_sec=300),
    )


def test_the_oracle_counts_each_departure_once_under_its_trips_service_date():
    """Six departures: -61, 60, 61, 300, 301, 0. The 24:06 one is the
    service date's, not the next calendar day's."""
    found = oracle.outcome(_scenario())
    assert oracle.by_day(found) == {
        DAY: oracle.Figures(
            departures=6,
            delay_sum_sec=661,
            on_time=3,
            over_five_min=1,
            worst_delay_sec=301,
            trip_runs=2,
            early=1,
            on_time_industry=4,
        )
    }
    figures = oracle.by_route(found)["R"]
    assert figures.mean_delay_sec == Fraction(661, 6)
    assert figures.on_time_share == Fraction(1, 2)
    assert figures.over_five_min_share == Fraction(1, 6)
    assert figures.early_share == Fraction(1, 6)
    assert figures.on_time_industry_share == Fraction(2, 3)


def _decoded(scenario_polls):
    return [
        (p.captured_at, [(trip, seq, delay) for trip, _route, seq, delay, *_ in _decode_rows(p.payload)])
        for p in scenario_polls
    ]


def test_a_perfect_feed_lists_only_stops_not_yet_left_each_with_its_true_delay():
    scenario = _scenario()
    truth = {
        (run.trip_id, seq): (service_day_instant(run.service_date, actual), actual - scheduled)
        for run in scenario.runs
        for seq, (scheduled, actual) in enumerate(
            zip(scenario.trip(run.trip_id, run.service_date).scheduled_sec, run.actual_sec, strict=True), start=1
        )
        if actual is not None
    }
    reported = set()
    for captured_at, rows in _decoded(polls(scenario)):
        for trip, seq, delay in rows:
            departs, true_delay = truth[(trip, seq)]
            assert captured_at < departs, (trip, seq, captured_at)
            assert delay == true_delay
            reported.add((trip, seq))
    assert reported == truth.keys()


def test_polls_carry_the_collectors_utc_file_name_and_their_stop_count():
    scenario_polls = polls(_scenario())
    first = scenario_polls[0]
    # The 07:00 trip leaves at 06:58:59 and enters the feed five minutes
    # earlier; the first poll on the minute grid after that is 06:54 JST.
    assert first.captured_at == datetime(2026, 5, 31, 21, 54, tzinfo=timezone.utc)
    assert first.source_file == "20260531/TripUpdate_215400.pb"
    assert all(p.stop_updates == len(list(_decode_rows(p.payload))) for p in scenario_polls)


def test_a_feed_too_sparse_to_report_a_departure_is_refused():
    with pytest.raises(ValueError, match="no poll"):
        polls(_scenario(FeedModel(poll_interval_sec=600, lead_sec=60)))


def test_a_scheduled_trip_without_a_run_is_refused():
    with pytest.raises(ValueError, match="has no run"):
        Scenario(
            name="gap",
            routes=(_ROUTE,),
            timetable=(_MORNING, _EVENING),
            days=(ServiceDay(DAY, "平日"),),
            runs=(TripRun.from_delays(_MORNING, DAY, (0, 0, 0)),),
        )


def test_a_run_that_leaves_a_stop_before_the_previous_one_is_refused():
    with pytest.raises(ValueError, match="strictly increase"):
        Scenario(
            name="backwards",
            routes=(_ROUTE,),
            timetable=(_MORNING,),
            days=(ServiceDay(DAY, "平日"),),
            runs=(TripRun.from_delays(_MORNING, DAY, (500, 0, 0)),),
        )


NEXT = DAY + timedelta(days=1)
_REVISED = ScheduledTrip("R_0700", "R", "平日", ("07:30:00", "07:38:00", "07:46:00"))
_ADDED = ScheduledTrip("R_0900", "R", "平日", ("09:00:00", "09:08:00", "09:16:00"))
_B = TimetableVersion("timetable_b", NEXT, (_REVISED, _ADDED))


def _revised() -> Scenario:
    """The 07:00 trip moves to 07:30 from NEXT, the evening trip is dropped and a 09:00 trip added."""
    return Scenario(
        name="revised",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING),
        days=(ServiceDay(DAY, "平日"), ServiceDay(NEXT, "平日")),
        runs=(
            TripRun.from_delays(_MORNING, DAY, (0, 30, 60)),
            TripRun.from_delays(_EVENING, DAY, (0, 0, 0)),
            TripRun.from_delays(_REVISED, NEXT, (0, 30, 60)),
            TripRun.from_delays(_ADDED, NEXT, (10, 10, 10)),
        ),
        revisions=(_B,),
    )


def _zip_rows(payload: bytes, name: str) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return list(csv.DictReader(io.StringIO(zf.read(name).decode())))


def test_a_revision_is_in_force_from_its_first_day_and_ships_as_its_own_zip():
    scenario = _revised()
    assert [v.name for v in scenario.versions] == ["scenario_revised", "timetable_b"]
    assert scenario.trip("R_0700", DAY).departures[0] == "07:00:00"
    assert scenario.trip("R_0700", NEXT).departures[0] == "07:30:00"
    found = oracle.outcome(scenario)
    assert found.day_versions == {DAY: "scenario_revised", NEXT: "timetable_b"}
    assert found.version_trips == {"scenario_revised": 2, "timetable_b": 2}
    assert oracle.revision_boundaries(found) == [NEXT]
    assert {(d.service_date, d.trip_id, d.scheduled_sec) for d in found.departures if d.stop_sequence == 1} == {
        (DAY, "R_0700", gtfs_seconds("07:00:00")),
        (DAY, "R_2350", gtfs_seconds("23:50:00")),
        (NEXT, "R_0700", gtfs_seconds("07:30:00")),
        (NEXT, "R_0900", gtfs_seconds("09:00:00")),
    }
    first, second = (static_gtfs_zip(scenario, v) for v in scenario.versions)
    assert _zip_rows(first, "calendar_dates.txt") == [{"service_id": "平日", "date": "20260601", "exception_type": "1"}]
    assert _zip_rows(second, "calendar_dates.txt") == [
        {"service_id": "平日", "date": "20260602", "exception_type": "1"}
    ]
    assert {r["trip_id"] for r in _zip_rows(second, "trips.txt")} == {"R_0700", "R_0900"}
    assert {r["route_id"] for r in _zip_rows(second, "routes.txt")} == {"R"}


def test_polls_are_batched_under_the_timetable_loaded_when_they_were_captured():
    batches = poll_batches(_revised())
    load = datetime(2026, 6, 2, 3, 0, tzinfo=JST)
    assert [version.name for version, _ in batches] == ["scenario_revised", "timetable_b"]
    assert all(p.captured_at < load and p.service_dates == {DAY} for p in batches[0][1])
    assert all(p.captured_at >= load and p.service_dates == {NEXT} for p in batches[1][1])
    assert sum(len(b) for _, b in batches) == len(polls(_revised()))


def test_a_revision_loaded_after_its_days_polls_began_is_refused():
    late = TimetableVersion(_B.name, NEXT, _B.trips, loaded_at=datetime(2026, 6, 2, 8, 0, tzinfo=JST))
    scenario = Scenario(**{**_revised().__dict__, "revisions": (late,)})
    with pytest.raises(ValueError, match="before its timetable is loaded"):
        poll_batches(scenario)


def _statuses(scenario_polls):
    """Per poll, each TripUpdate's (trip_id, trip status, [(seq, departure delay, stop status)])."""
    return [
        (
            p.captured_at,
            [(t.trip_id, t.status, [(e.stop_sequence, e.departure_delay, e.status) for e in t.stops]) for t in p.trips],
        )
        for p in scenario_polls
    ]


def _wire(payload: bytes):
    """Each TripUpdate of a payload as decoded from its bytes: (trip_id,
    route_id, start_date, trip status, [(seq, stop_id, departure delay, stop status)])."""
    out = []
    for entity in _fields(payload).get(2, []):
        tu = _fields(_fields(entity)[3][0])
        trip = _fields(tu[1][0])
        stops = []
        for raw in tu.get(2, []):
            stu = _fields(raw)
            delay = _int32(_fields(stu[3][0]).get(1, [None])[0]) if 3 in stu else None
            stops.append((stu[1][0], stu[4][0].decode(), delay, stu.get(5, [None])[0]))
        out.append((trip[1][0].decode(), trip[5][0].decode(), trip[3][0].decode(), trip.get(4, [None])[0], stops))
    return out


def test_a_polls_wire_bytes_carry_exactly_its_entries():
    """Every kind of entry, round-tripped: departures, skips predicted then
    announced, and each form of a trip reported not running."""
    scenario = Scenario(
        name="every_kind",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING, ScheduledTrip("R_2300", "R", "平日", ("23:00:00", "23:08:00", "23:16:00"))),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_MORNING, DAY, (-61, None, 61), skips_announced_sec=gtfs_seconds("06:58:00")),),
        cancellations=(
            Cancellation("R_2350", DAY, announced_sec=gtfs_seconds("23:47:00"), predicted_delay_sec=30),
            Cancellation("R_2300", DAY, reported_as="canceled_without_stops"),
        ),
    )
    statuses = set()
    for poll in polls(scenario):
        assert poll.trips
        expected = [
            (
                t.trip_id,
                t.route_id,
                t.service_date.strftime("%Y%m%d"),
                t.status,
                [(e.stop_sequence, e.stop_id, e.departure_delay, e.status) for e in t.stops],
            )
            for t in poll.trips
        ]
        assert _wire(poll.payload) == expected
        assert poll.stop_updates == len(list(_decode_rows(poll.payload)))
        statuses |= {(t[3], e[3]) for t in expected for e in t[4]} | {(t[3], None) for t in expected if not t[4]}
    assert statuses == {(SCHEDULED, SCHEDULED), (SCHEDULED, SKIPPED), (CANCELED, NO_DATA), (CANCELED, None)}


def _skipping(announced: int | None) -> Scenario:
    return Scenario(
        name="skips",
        routes=(_ROUTE,),
        timetable=(_MORNING,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_MORNING, DAY, (30, None, 90), skips_announced_sec=announced),),
    )


def test_a_skipped_stop_is_predicted_until_announced_then_reported_skipped_and_is_no_departure():
    announced = gtfs_seconds("06:58:00")
    seen = {}
    for captured_at, trips in _statuses(polls(_skipping(announced))):
        ((_trip_id, trip_status, stops),) = trips
        assert trip_status == SCHEDULED
        for seq, delay, stop_status in stops:
            if seq == 2:
                before = captured_at < service_day_instant(DAY, announced)
                # Until announced it is predicted at the delay of stop 1, the one before it.
                assert (delay, stop_status) == ((30, SCHEDULED) if before else (None, SKIPPED))
                seen[before] = True
            else:
                assert stop_status == SCHEDULED
    assert seen == {True: True, False: True}
    scenario = _skipping(announced)
    found = oracle.outcome(scenario)
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, 30), (3, 90)]
    # A run that skipped a stop still ran: executed, and observed.
    assert found.service == {DAY: oracle.Service(planned=1, reported_not_run=0, observed=1)}


def test_a_skipped_first_stop_is_predicted_at_the_delay_of_the_next_stop_served():
    announced = gtfs_seconds("06:58:00")
    scenario = Scenario(
        name="skips_first",
        routes=(_ROUTE,),
        timetable=(_MORNING,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_MORNING, DAY, (None, 40, 90), skips_announced_sec=announced),),
    )
    first_stop = {
        captured_at: next((delay, status) for seq, delay, status in stops if seq == 1)
        for captured_at, ((_trip_id, _status, stops),) in _statuses(polls(scenario))
        if any(seq == 1 for seq, _delay, _status in stops)
    }
    assert first_stop
    for captured_at, listed in first_stop.items():
        assert listed == ((40, SCHEDULED) if captured_at < service_day_instant(DAY, announced) else (None, SKIPPED))
    assert {listed for listed in first_stop.values()} == {(40, SCHEDULED), (None, SKIPPED)}
    # Listed until the run leaves stop 2, the next it serves.
    assert max(first_stop) < service_day_instant(DAY, gtfs_seconds("07:08:40"))
    assert [(d.stop_sequence, d.delay_sec) for d in oracle.outcome(scenario).departures] == [(2, 40), (3, 90)]


def test_a_skip_announced_only_after_the_stop_is_passed_is_refused():
    with pytest.raises(ValueError, match="no poll reports"):
        polls(_skipping(gtfs_seconds("07:30:00")))


def _cancelling(cancellation: Cancellation) -> Scenario:
    return Scenario(
        name="cancels",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_EVENING, DAY, (0, 0, 0)),),
        cancellations=(cancellation,),
    )


def test_a_cancellation_is_predicted_until_announced_then_reported_canceled_with_its_stops_left_without_data():
    announced = gtfs_seconds("06:57:00")
    scenario = _cancelling(Cancellation("R_0700", DAY, announced_sec=announced, predicted_delay_sec=90))
    morning = [(at, trip) for at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    for at, (_trip_id, trip_status, stops) in morning:
        if at < service_day_instant(DAY, announced):
            assert trip_status == SCHEDULED
            assert {(delay, status) for _seq, delay, status in stops} == {(90, SCHEDULED)}
        else:
            assert trip_status == CANCELED
            assert stops
            assert {(delay, status) for _seq, delay, status in stops} == {(None, NO_DATA)}
    found = oracle.outcome(scenario)
    assert {d.trip_id for d in found.departures} == {"R_2350"}
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}
    assert found.service[DAY].executed == 1
    assert oracle.delivered_rate_shown(found, DAY)


def test_a_cancellation_may_list_no_stop_at_all():
    """Ingest stores such a cancellation as a row per stop not due yet:
    three before the trip sets off, one once only its last stop is left."""
    scenario = _cancelling(Cancellation("R_0700", DAY, reported_as="canceled_without_stops"))
    morning = [trip for _at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    assert all(trip_status == CANCELED and stops == [] for _trip_id, trip_status, stops in morning)
    assert all(p.stop_updates == len(list(_decode_rows(p.payload))) for p in polls(scenario))
    stored = {p.captured_at: p.stored_rows for p in polls(scenario)}
    assert stored[service_day_instant(DAY, gtfs_seconds("06:58:00"))] == 3
    assert stored[service_day_instant(DAY, gtfs_seconds("07:08:00"))] == 2
    assert stored[service_day_instant(DAY, gtfs_seconds("07:10:00"))] == 1
    assert oracle.outcome(scenario).service == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


def test_a_cancellation_may_be_predicted_and_then_list_no_stop():
    announced = gtfs_seconds("06:57:00")
    scenario = _cancelling(
        Cancellation(
            "R_0700", DAY, announced_sec=announced, reported_as="canceled_without_stops", predicted_delay_sec=90
        )
    )
    morning = [(at, trip) for at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert {at < service_day_instant(DAY, announced) for at, _trip in morning} == {True, False}
    for at, (_trip_id, trip_status, stops) in morning:
        if at < service_day_instant(DAY, announced):
            assert trip_status == SCHEDULED
            assert {(delay, status) for _seq, delay, status in stops} == {(90, SCHEDULED)}
        else:
            assert (trip_status, stops) == (CANCELED, [])
    found = oracle.outcome(scenario)
    assert {d.trip_id for d in found.departures} == {"R_2350"}
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


def test_a_trip_reported_with_every_stop_skipped_did_not_run():
    scenario = _cancelling(Cancellation("R_0700", DAY, reported_as="every_stop_skipped"))
    morning = [trip for _at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    for _trip_id, trip_status, stops in morning:
        assert trip_status == SCHEDULED
        assert stops
        assert {(delay, status) for _seq, delay, status in stops} == {(None, SKIPPED)}
    found = oracle.outcome(scenario)
    assert {d.trip_id for d in found.departures} == {"R_2350"}
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


def test_a_trip_may_be_reported_skipped_at_every_stop_and_then_without_data_once_due():
    scenario = _cancelling(Cancellation("R_0700", DAY, reported_as="skipped_then_no_data"))
    morning = [(at, trip) for at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    statuses = set()
    for _at, (_trip_id, trip_status, stops) in morning:
        assert trip_status == SCHEDULED
        assert [seq for seq, _delay, _status in stops] == [1, 2, 3]
        statuses |= {(delay, status) for _seq, delay, status in stops}
    assert statuses == {(None, SKIPPED), (None, NO_DATA)}
    found = oracle.outcome(scenario)
    assert {d.trip_id for d in found.departures} == {"R_2350"}
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


def test_an_unreported_trip_is_in_no_poll_and_counts_as_executed_but_not_observed():
    scenario = Scenario(
        name="silent",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_EVENING, DAY, (0, 0, 0)),),
        unreported=(Unreported("R_0700", DAY),),
    )
    assert {trip[0] for _at, trips in _statuses(polls(scenario)) for trip in trips} == {"R_2350"}
    # Silence is not evidence the trip did not run: it counts as executed,
    # and only coverage, one observed trip of two planned, shows the gap.
    found = oracle.outcome(scenario)
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=0, observed=1)}
    assert found.service[DAY].executed == 2
    assert not oracle.delivered_rate_shown(found, DAY)


def _every(first: str, n: int, headway_sec: int, prefix: str) -> tuple[ScheduledTrip, ...]:
    """*n* trips of ``_ROUTE`` *headway_sec* apart from *first*, eight minutes between stops."""
    start = gtfs_seconds(first)
    return tuple(
        ScheduledTrip(
            f"{prefix}{k}",
            "R",
            "平日",
            tuple(gtfs_clock(start + k * headway_sec + i * 480) for i in range(len(_STOPS))),
        )
        for k in range(n)
    )


# Four trips six minutes apart. The first runs late from the second stop on,
# so the second overtakes it there and at the last stop; the third does not
# run and the feed never mentions it; the fourth skips the middle stop.
_SIX_MINUTES = _every("07:00:00", 4, 360, "R_")


def _bunched_runs(day: date) -> tuple[TripRun, ...]:
    a, b, _c, d = _SIX_MINUTES
    return (
        TripRun.from_delays(a, day, (0, 400, 400)),
        TripRun.from_delays(b, day, (0, 0, 30)),
        TripRun.from_delays(d, day, (0, None, -50)),
    )


def _bunched(*, next_day: tuple[TripRun, ...] | None = None, revisions=()) -> Scenario:
    days = (ServiceDay(DAY, "平日"),) if next_day is None else (ServiceDay(DAY, "平日"), ServiceDay(NEXT, "平日"))
    unreported = [Unreported("R_2", DAY)]
    return Scenario(
        name="bunched",
        routes=(_ROUTE,),
        timetable=_SIX_MINUTES,
        days=days,
        runs=(*_bunched_runs(DAY), *(next_day or ())),
        unreported=tuple(unreported),
        revisions=revisions,
    )


def test_the_oracle_takes_each_stops_gaps_in_departure_order_and_pools_them_over_the_route():
    """Stop 1 leaves at 07:00, 07:06 and 07:18: gaps 360 and 720, the missing
    trip's slot open. Stop 2 sees the second trip at 07:14:00 and the late
    first at 07:14:40, the fourth skipping it: one gap of 40. Stop 3 sees
    07:22:30, 07:22:40 and 07:33:10: gaps 10 and 630. The timetable plans
    360 s at every stop, the missing trip included, so 630 = 1.75 x 360 is
    not long and 720 is."""
    found = oracle.outcome(_bunched())
    assert found.plans == {("R", DAY): oracle.Plan(gaps=(360,) * 9, headway_sec=Fraction(360))}
    assert found.wait_plans == found.plans
    day = oracle.headway_days(found)[("R", DAY)]
    assert day == oracle.HeadwayDay(gaps=(10, 40, 360, 630, 720), long_gaps=1)
    assert (day.samples, day.median_sec, day.sum_sec, day.sumsq_sec2) == (5, 360, 1760, 1046600)

    quality = oracle.headway_quality(found, DAY, DAY)
    assert list(quality) == ["R"]
    # Waits: 1046600 / (2 x 1760) actual, 9 x 360^2 / (2 x 9 x 360) = 180 scheduled.
    assert quality["R"].actual_wait_sec == Fraction(26165, 88)
    assert quality["R"].scheduled_wait_sec == 180
    assert quality["R"].excess_wait_sec == Fraction(10325, 88)
    # Mean 352, mean square 209320: variance 85416.
    assert quality["R"].cov_squared == Fraction(85416, 352**2)
    assert quality["R"].long_gap_share == Fraction(1, 5)
    assert quality["R"].samples == 5
    assert not oracle.no_high_frequency_route(found, DAY, DAY)


def test_the_oracle_pools_a_periods_days_by_their_sums_not_by_averaging_each_days_figures():
    """A second day running every trip on time adds nine gaps of 360 s."""
    on_time = tuple(TripRun.from_delays(t, NEXT, (0, 0, 0)) for t in _SIX_MINUTES)
    found = oracle.outcome(_bunched(next_day=on_time))
    assert oracle.headway_days(found)[("R", NEXT)] == oracle.HeadwayDay(gaps=(360,) * 9, long_gaps=0)
    pooled = oracle.headway_quality(found, DAY, NEXT)["R"]
    assert (pooled.samples, pooled.sum_sec, pooled.sumsq_sec2, pooled.long_gaps) == (14, 5000, 2213000, 1)
    assert pooled.excess_wait_sec == Fraction(413, 10)
    assert pooled.long_gap_share == Fraction(1, 14)


def test_each_day_is_judged_by_the_timetable_in_force_that_day():
    """From the second day the trips run fourteen minutes apart, not
    high-frequency, so a period pools the first day alone. The second day's
    long gaps are judged against 1.75 x 840 = 1470 s: 1480 is long, the
    840 s gaps are not, as they would be against the first day's 630."""
    revised = _every("07:00:00", 3, 840, "S_")
    revision = TimetableVersion("fourteen_minutes", NEXT, revised)
    runs = tuple(
        TripRun.from_delays(t, NEXT, d) for t, d in zip(revised, ((0, 0, 0), (0, 0, 640), (0, 0, 0)), strict=True)
    )
    found = oracle.outcome(_bunched(next_day=runs, revisions=(revision,)))
    assert found.plans[("R", NEXT)] == oracle.Plan(gaps=(840,) * 6, headway_sec=Fraction(840))
    assert oracle.headway_days(found)[("R", NEXT)] == oracle.HeadwayDay(
        gaps=(200, 840, 840, 840, 840, 1480), long_gaps=1
    )
    assert oracle.headway_quality(found, DAY, NEXT) == oracle.headway_quality(found, DAY, DAY)
    assert oracle.headway_quality(found, NEXT, NEXT) == {}
    assert oracle.no_high_frequency_route(found, NEXT, NEXT)


def test_the_median_of_an_even_count_is_the_mean_of_its_middle_two():
    assert oracle.median([630, 10, 360, 40]) == 200
    assert oracle.median([10, 40, 360]) == 40
    assert oracle.median([]) is None


def test_two_departures_from_one_stop_in_the_same_second_are_refused():
    a, b, _c, d = _SIX_MINUTES
    runs = (
        TripRun.from_delays(a, DAY, (360, 360, 360)),
        TripRun.from_delays(b, DAY, (0, 0, 0)),
        TripRun.from_delays(d, DAY, (0, 0, 0)),
    )
    scenario = Scenario(
        name="same_second",
        routes=(_ROUTE,),
        timetable=_SIX_MINUTES,
        days=(ServiceDay(DAY, "平日"),),
        runs=runs,
        unreported=(Unreported("R_2", DAY),),
    )
    with pytest.raises(ValueError, match="same second"):
        oracle.headway_days(oracle.outcome(scenario))


def _one_run(delays: tuple[int, ...], feed: FeedModel | None = None, *, evening: tuple[int, ...] | None = None):
    trips = (_MORNING,) if evening is None else (_MORNING, _EVENING)
    runs = (TripRun.from_delays(_MORNING, DAY, delays),)
    if evening is not None:
        runs += (TripRun.from_delays(_EVENING, DAY, evening),)
    return Scenario(
        name="readings",
        routes=(_ROUTE,),
        timetable=trips,
        days=(ServiceDay(DAY, "平日"),),
        runs=runs,
        feed=feed or FeedModel(poll_interval_sec=60, lead_sec=300),
    )


def test_a_true_delay_beyond_the_clamp_either_way_is_no_departure_and_each_of_its_readings_is_implausible():
    """The run leaves at 05:00:00, 05:07:59 and 09:16:00 and is in the feed
    from 04:55. Stop 1 is read 5 times at -7200 s, stop 2 13 times at
    -7201 s and stop 3 261 times at 7200 s: 279 readings, 13 implausible."""
    found = oracle.outcome(_one_run((-7200, -7201, 7200)))
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, -7200), (3, 7200)]
    assert found.readings == {DAY: oracle.Readings(readings=279, implausible=13, rows=279)}
    assert found.service == {DAY: oracle.Service(planned=1, reported_not_run=0, observed=1)}
    share = oracle.readings_over(found, DAY, DAY).clamp_share
    assert share == Fraction(1300, 279)
    assert not oracle.clamp_high(share)
    assert oracle.scorecard(found) == {
        date(2026, 6, 1): oracle.Scorecard(last=DAY, readings=oracle.Readings(279, 13, 279), polls=261, ages=(0, 0))
    }


def _misreporting() -> Scenario:
    """Both trips run on time. From 07:05 the 07:00 run's stop 2 is reported
    7201 s late and from 07:10 its stop 3 7200 s late; the evening run is
    reported a day early throughout."""
    feed = FeedModel(
        poll_interval_sec=60,
        lead_sec=300,
        misreports=(
            Misreport("R_0700", DAY, 7201, gtfs_seconds("07:05:00"), gtfs_seconds("30:00:00"), (2,)),
            Misreport("R_0700", DAY, 7200, gtfs_seconds("07:10:00"), gtfs_seconds("30:00:00"), (3,)),
            Misreport("R_2350", DAY, -86400, 0, gtfs_seconds("30:00:00")),
        ),
    )
    return _one_run((0, 0, 0), feed, evening=(0, 0, 0))


def test_a_misreport_beyond_the_clamp_leaves_the_last_plausible_reading_standing():
    """The 07:00 run's stop 2 is read at 7201 s 3 times before it leaves at
    07:08, so its reading of 07:04 stands. Its stop 3's 7200 s is plausible
    and so its last reading. The evening run is read a day early in each of
    its 39 readings, so it has no departure and is not observed."""
    scenario = _misreporting()
    stop_2 = {
        p.captured_at: s.departure_delay
        for p in polls(scenario)
        for t in p.trips
        for s in t.stops
        if (t.trip_id, s.stop_sequence) == ("R_0700", 2)
    }
    assert stop_2[service_day_instant(DAY, gtfs_seconds("07:04:00"))] == 0
    assert stop_2[service_day_instant(DAY, gtfs_seconds("07:05:00"))] == 7201
    found = oracle.outcome(scenario)
    assert [(d.trip_id, d.stop_sequence, d.delay_sec) for d in found.departures] == [
        ("R_0700", 1, 0),
        ("R_0700", 2, 0),
        ("R_0700", 3, 7200),
    ]
    assert found.readings == {DAY: oracle.Readings(readings=78, implausible=42, rows=78)}
    assert found.service == {DAY: oracle.Service(planned=2, reported_not_run=0, observed=1)}
    assert oracle.clamp_high(oracle.readings_over(found, DAY, DAY).clamp_share)


def _relisted(from_clock: str, until_clock: str) -> Scenario:
    """The 07:00 run, a minute late at each stop, listed again at 0 s from
    *from_clock* until *until_clock*."""
    relisting = Relisting("R_0700", DAY, 0, gtfs_seconds(from_clock), gtfs_seconds(until_clock))
    return _one_run((60, 60, 60), FeedModel(poll_interval_sec=60, lead_sec=300, relistings=(relisting,)))


def test_a_reading_captured_more_than_130_minutes_after_its_stops_departure_is_no_reading_of_it():
    """Listed again from 09:11 to 09:30, each poll lists all three stops at
    0 s: 60 readings beside the run's own 42, every one still a reading the
    feed sent. Stop 1, scheduled at 07:00, takes none of them, as 09:11 is
    past 09:10. Stops 2 and 3, scheduled at 07:08 and 07:16, take those of
    09:18 and 09:26 at the latest, so each is read last at 0 s, 7,740 s after
    the run left it."""
    scenario = _relisted("09:11:00", "09:31:00")
    relisted = [p for p in polls(scenario) if p.captured_at >= service_day_instant(DAY, gtfs_seconds("09:11:00"))]
    assert len(relisted) == 20
    assert {(s.stop_sequence, s.departure_delay) for p in relisted for t in p.trips for s in t.stops} == {
        (1, 0),
        (2, 0),
        (3, 0),
    }
    found = oracle.outcome(scenario)
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, 60), (2, 0), (3, 0)]
    assert [(p.departure.stop_sequence, p.lead_sec) for p in found.predictions] == [(1, 60), (2, -7740), (3, -7740)]
    assert found.readings == {DAY: oracle.Readings(readings=102, implausible=0, rows=102)}
    later = oracle.outcome(_relisted("09:27:00", "09:47:00"))
    assert [(d.stop_sequence, d.delay_sec) for d in later.departures] == [(1, 60), (2, 60), (3, 60)]


def test_a_reading_is_filed_by_its_capture_not_by_the_run_its_poll_names():
    """A reading is filed under its capture's service day, or the day either
    side whose departure of its stop is within four hours of it. The relisted
    scenario's evening listing of the second day's 09:10 run, captured at
    21:30 on the first, is filed under the first day, past that day's 09:10
    departure by more than 130 minutes, so it is no reading of either day's
    departure, and each day's 09:10 run keeps its own last reading."""
    clock = gtfs_seconds
    assert oracle.filed_day(service_day_instant(DAY, clock("21:30:00")), clock("09:10:00")) == DAY
    assert oracle.filed_day(service_day_instant(DAY, clock("27:58:00")), clock("04:05:00")) == DAY + timedelta(days=1)
    assert oracle.filed_day(service_day_instant(DAY, clock("28:03:00")), clock("27:56:00")) == DAY
    scenario = relisted(DAY)
    found = oracle.outcome(scenario)
    first_stops = {
        (d.service_date, d.delay_sec) for d in found.departures if (d.trip_id, d.stop_sequence) == ("L1_0910", 1)
    }
    assert first_stops == {(DAY, 240), (DAY + timedelta(days=1), -30)}


def test_relistings_the_feed_model_cannot_follow_are_refused():
    relisting = Relisting("R_0700", DAY, 0, gtfs_seconds("09:00:00"), gtfs_seconds("09:20:00"))
    with pytest.raises(ValueError, match="only a run can be listed again"):
        _one_run((0, 0, 0), FeedModel(relistings=(replace(relisting, trip_id="R_2350"),)))
    with pytest.raises(ValueError, match="overlaps the run in the feed"):
        _one_run((0, 0, 0), FeedModel(relistings=(replace(relisting, from_sec=gtfs_seconds("07:10:00")),)))
    with pytest.raises(ValueError, match="overlaps the run in the feed or another relisting"):
        _one_run((0, 0, 0), FeedModel(relistings=(relisting, replace(relisting, until_sec=gtfs_seconds("09:10:00")))))
    with pytest.raises(ValueError, match="must end after it starts"):
        _one_run((0, 0, 0), FeedModel(relistings=(replace(relisting, until_sec=relisting.from_sec),)))
    with pytest.raises(ValueError, match="starts at a stop the trip does not have"):
        _one_run((0, 0, 0), FeedModel(relistings=(replace(relisting, first_stop=4),)))
    with pytest.raises(ValueError, match="predicts perfectly and reports no arrivals"):
        _one_run((0, 0, 0), FeedModel(freshness_lag_sec=30, relistings=(relisting,)))
    skipping = TripRun.from_delays(_MORNING, DAY, (0, None, 0))
    with pytest.raises(ValueError, match="skips a stop and cannot be listed again"):
        replace(_one_run((0, 0, 0), FeedModel(relistings=(relisting,))), runs=(skipping,))
    # Between two polls a minute apart.
    between = replace(relisting, from_sec=gtfs_seconds("09:00:10"), until_sec=gtfs_seconds("09:00:50"))
    with pytest.raises(ValueError, match="no poll carries these relistings"):
        polls(_one_run((0, 0, 0), FeedModel(poll_interval_sec=60, relistings=(between,))))


def _frozen(header: Literal["frozen", "advancing"]) -> Scenario:
    freeze = Freeze(DAY, gtfs_seconds("06:57:00"), gtfs_seconds("07:03:00"), header)
    return _one_run((0, 0, 0), FeedModel(poll_interval_sec=60, lead_sec=300, freezes=(freeze,)))


def test_a_frozen_feed_resends_one_poll_header_and_all_so_each_repeat_reads_a_minute_older():
    """The 06:57 poll is resent at 06:58 to 07:02, still listing stop 1 after
    the run leaves it at 07:00. Readings: 3 in each of the 8 polls to 07:02,
    then 2 in the 5 to 07:07 and 1 in the 8 to 07:15, 42 in all, for 3
    departures. The 21 polls' ages are 16 zeros and 60 to 300 s."""
    scenario = _frozen("frozen")
    sent = {p.captured_at: p for p in polls(scenario)}
    first = sent[service_day_instant(DAY, gtfs_seconds("06:57:00"))]
    for k in range(1, 6):
        repeat = sent[first.captured_at + timedelta(minutes=k)]
        assert repeat.payload == first.payload
        assert repeat.header == first.captured_at
    stale = sent[service_day_instant(DAY, gtfs_seconds("07:02:00"))]
    assert [s.stop_sequence for t in stale.trips for s in t.stops] == [1, 2, 3]
    found = oracle.outcome(scenario)
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, 0), (2, 0), (3, 0)]
    assert found.readings == {DAY: oracle.Readings(readings=42, implausible=0, rows=42)}
    assert sorted(age for _day, age in found.ages) == [0] * 16 + [60, 120, 180, 240, 300]
    # Rank 20 x 0.5 = 10 is a zero; rank 20 x 0.95 = 19 is the fourth repeat.
    assert oracle.scorecard(found)[date(2026, 6, 1)].ages == (0, 240)


def test_a_feed_whose_predictions_stop_moving_under_an_advancing_header_reads_no_age():
    scenario = _frozen("advancing")
    sent = {p.captured_at: p for p in polls(scenario)}
    first = sent[service_day_instant(DAY, gtfs_seconds("06:57:00"))]
    repeat = sent[service_day_instant(DAY, gtfs_seconds("07:02:00"))]
    assert repeat.trips == first.trips
    assert repeat.header == repeat.captured_at
    assert repeat.payload != first.payload
    found = oracle.outcome(scenario)
    assert found.readings == {DAY: oracle.Readings(readings=42, implausible=0, rows=42)}
    assert {age for _day, age in found.ages} == {0}


def test_the_clamp_share_is_flagged_as_printed_to_two_decimals_then_to_a_tenth():
    assert not oracle.clamp_high(Fraction(504, 100))
    assert oracle.clamp_high(Fraction(505, 100))
    # 5.046% prints 5.05%, which prints 5.1%, though 5.046% is nearer 5.0%.
    assert oracle.clamp_high(Fraction(5046, 1000))
    assert not oracle.clamp_high(Fraction(5044, 1000))
    assert not oracle.clamp_high(None)


def test_a_quantile_interpolates_linearly_between_the_two_closest_ranks():
    assert oracle.quantile([300, 0, 120, 60], Fraction(1, 2)) == 90
    # Rank 3 x 0.95 = 2.85: 120 and 85% of the way on to 300.
    assert oracle.quantile([300, 0, 120, 60], Fraction(19, 20)) == 273
    assert oracle.quantile([5, 1, 3], Fraction(1, 2)) == 3


def test_a_reading_files_under_the_service_day_its_poll_was_captured_in():
    assert oracle.service_day_of(datetime(2026, 6, 2, 3, 59, 59, tzinfo=JST)) == DAY
    assert oracle.service_day_of(datetime(2026, 6, 2, 4, 0, tzinfo=JST)) == DAY + timedelta(days=1)
    # The evening run's polls run past midnight, to 00:05 on the next calendar day.
    found = oracle.outcome(_one_run((0, 0, 0), evening=(0, 0, 0)))
    assert set(found.readings) == {DAY}


def test_misreports_and_freezes_the_oracle_cannot_follow_are_refused():
    skipping = TripRun.from_delays(_MORNING, DAY, (0, None, 0))
    late = Misreport("R_0700", DAY, 9000, 0, gtfs_seconds("30:00:00"))
    with pytest.raises(ValueError, match="skips a stop"):
        replace(_one_run((0, 0, 0), FeedModel(misreports=(late,))), runs=(skipping,))
    with pytest.raises(ValueError, match="only a run"):
        _one_run((0, 0, 0), FeedModel(misreports=(replace(late, trip_id="R_2350"),)))
    freeze = Freeze(DAY, gtfs_seconds("06:57:00"), gtfs_seconds("07:03:00"))
    with pytest.raises(ValueError, match="neither cancel"):
        Scenario(
            name="frozen_cancellation",
            routes=(_ROUTE,),
            timetable=(_MORNING, _EVENING),
            days=(ServiceDay(DAY, "平日"),),
            runs=(TripRun.from_delays(_MORNING, DAY, (0, 0, 0)),),
            cancellations=(Cancellation("R_2350", DAY),),
            feed=FeedModel(freezes=(freeze,)),
        )
    with pytest.raises(ValueError, match="must not overlap"):
        _one_run((0, 0, 0), FeedModel(freezes=(freeze, replace(freeze, from_sec=gtfs_seconds("07:00:00")))))
    idle = Misreport("R_0700", DAY, 9000, gtfs_seconds("05:00:00"), gtfs_seconds("06:00:00"))
    with pytest.raises(ValueError, match="no poll carries"):
        polls(_one_run((0, 0, 0), FeedModel(misreports=(idle,))))
    # The scorecard's window ends at the newest day with a departure.
    with pytest.raises(ValueError, match="needs a departure"):
        oracle.scorecard(oracle.outcome(_one_run((7300, 7300, 7300))))
    # A misreport of the delay the feed would send anyway changes no reading.
    truthful = Misreport("R_0700", DAY, 60, gtfs_seconds("06:55:00"), gtfs_seconds("07:20:00"), (2,))
    with pytest.raises(ValueError, match="no poll carries"):
        polls(_one_run((0, 60, 0), FeedModel(misreports=(truthful,))))
    # The evening run enters the feed at 23:45, inside a freeze of the 23:40
    # poll that lasts past its first departure, which no poll then reports.
    earlier = ScheduledTrip("R_2330", "R", "平日", ("23:30:00", "23:38:00", "23:46:00"))
    swallowing = Freeze(DAY, gtfs_seconds("23:40:00"), gtfs_seconds("23:55:00"))
    scenario = Scenario(
        name="swallowed",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING, earlier),
        days=(ServiceDay(DAY, "平日"),),
        runs=tuple(TripRun.from_delays(t, DAY, (0, 0, 0)) for t in (_MORNING, _EVENING, earlier)),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300, freezes=(swallowing,)),
    )
    with pytest.raises(ValueError, match="no poll reports"):
        polls(scenario)


def test_the_scorecard_keeps_a_month_per_service_month_and_files_an_after_midnight_poll_under_its_service_day():
    """May 31 runs both trips and June 1 the morning one alone. The evening
    run's polls to 00:05 on June 1 are May 31's, and so are the repeats of a
    23:58 header frozen until 00:03: 78 readings and 42 polls in May, four of
    them 60 to 240 s old, against 39 readings and 21 polls in June."""
    may = date(2026, 5, 31)
    freeze = Freeze(may, gtfs_seconds("23:58:00"), gtfs_seconds("24:03:00"))
    scenario = Scenario(
        name="month_boundary",
        routes=(_ROUTE,),
        timetable=(_MORNING, _EVENING),
        days=(ServiceDay(may, "平日"), ServiceDay(DAY, "平日")),
        runs=(
            TripRun.from_delays(_MORNING, may, (0, 0, 0)),
            TripRun.from_delays(_EVENING, may, (0, 0, 0)),
            TripRun.from_delays(_MORNING, DAY, (0, 0, 0)),
        ),
        unreported=(Unreported("R_2350", DAY),),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300, freezes=(freeze,)),
    )
    found = oracle.outcome(scenario)
    assert found.readings == {
        may: oracle.Readings(readings=78, implausible=0, rows=78),
        DAY: oracle.Readings(readings=39, implausible=0, rows=39),
    }
    # Rank 41 x 0.95 = 38.95: the first repeat's 60 s and 95% of the way on to 120 s.
    assert oracle.scorecard(found) == {
        date(2026, 5, 1): oracle.Scorecard(last=may, readings=oracle.Readings(78, 0, 78), polls=42, ages=(0, 117)),
        date(2026, 6, 1): oracle.Scorecard(last=DAY, readings=oracle.Readings(39, 0, 39), polls=21, ages=(0, 0)),
    }


def test_a_feed_that_reports_no_arrival_sends_none_and_pairs_no_visit():
    """A feed that does not report arrivals sends none, and its timetable
    plans no dwell. Its visits have departures only, so a route-day holds no
    dwell or running sample."""
    scenario = _scenario()
    for poll in polls(scenario):
        assert all(e.arrival_delay is None for t in poll.trips for e in t.stops)
        assert all(row[5] is None for row in _decode_rows(poll.payload))
    stop_times = _zip_rows(static_gtfs_zip(scenario), "stop_times.txt")
    assert all(r["arrival_time"] == r["departure_time"] for r in stop_times)
    found = oracle.outcome(scenario)
    assert len(found.visits) == 6
    assert all(v.arrival_sec is None and not v.departure_observed for v in found.visits)
    assert oracle.dwell_run_by_route_day(found) == {("R", DAY, "平日"): oracle.DwellRun(dwell=(), running=())}


_QUAYS = tuple(Stop(f"Q{i}", f"Quay {i}", 34.1 + i / 1000, 132.0) for i in range(1, 5))
_QUAY_ROUTE = Route("Q", "Quay line", _QUAYS)
# Each stop's (arrival, departure), planned and then as the run went. The
# feed is polled on the minute.
_QUAY_PLAN = (("06:58:00", "07:00:00"), ("07:04:00", "07:04:30"), ("07:08:00", "07:08:20"), ("07:12:00", "07:12:00"))
_QUAY_RUN = (("06:57:30", "07:00:40"), ("07:04:10", "07:04:40"), ("07:08:50", "07:09:10"), ("07:12:20", "07:12:20"))


def _quay(
    run: tuple[tuple[str, str], ...] = _QUAY_RUN,
    plan: tuple[tuple[str, str], ...] = _QUAY_PLAN,
    feed: FeedModel | None = None,
) -> Scenario:
    trip = ScheduledTrip.from_clocks("Q_0700", "Q", "平日", plan)
    return Scenario(
        name="quay",
        routes=(_QUAY_ROUTE,),
        timetable=(trip,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_clocks(trip, DAY, run),),
        feed=feed or FeedModel(arrivals=True),
    )


def test_a_planned_dwell_ships_as_its_stops_arrival_time():
    rows = _zip_rows(static_gtfs_zip(_quay()), "stop_times.txt")
    assert [(r["arrival_time"], r["departure_time"]) for r in rows] == list(_QUAY_PLAN)


def test_a_feed_that_reports_arrivals_lists_each_stop_with_one_of_them_at_a_time():
    """The run enters the feed at 06:55. Stop 1, reached at 06:57:30, is
    predicted to leave at its arrival's delay, -30, until the 06:58 poll
    reports that arrival; the 06:59 and 07:00 polls report its true
    departure, +40. Stop 2 is reached and left between polls, so only its
    departure is ever reported, at +10. Stop 3 is reached at 07:08:50 and
    left at 07:09:10, so the 07:09 poll reports its arrival and no poll its
    departure after it. The last stop is only ever an arrival, +20."""
    listed = {
        f"{p.captured_at.astimezone(JST):%H:%M}": [
            (e.stop_sequence, e.arrival_delay, e.departure_delay) for t in p.trips for e in t.stops
        ]
        for p in polls(_quay())
    }
    ahead = [(2, None, 10), (3, None, 50), (4, 20, None)]
    assert listed == {
        **{m: [(1, None, -30), *ahead] for m in ("06:55", "06:56", "06:57")},
        "06:58": [(1, -30, None), *ahead],
        **{m: [(1, None, 40), *ahead] for m in ("06:59", "07:00")},
        **{f"07:0{m}": ahead for m in range(1, 5)},
        **{f"07:0{m}": ahead[1:] for m in range(5, 9)},
        "07:09": [(3, 50, None), (4, 20, None)],
        **{f"07:1{m}": [(4, 20, None)] for m in range(3)},
    }
    for poll in polls(_quay()):
        rows = [(seq, arr, dep) for _trip, _route, seq, dep, _stop, arr, *_ in _decode_rows(poll.payload)]
        assert rows == [(e.stop_sequence, e.arrival_delay, e.departure_delay) for t in poll.trips for e in t.stops]


def test_the_oracle_pairs_each_arrival_with_the_departure_reported_after_it():
    """Stop 1 dwells 07:00:40 - 06:57:30 = 190 s against a planned 120, so
    the difference of its delays, 70, is not its dwell. Stop 2 has no
    arrival, so neither figure, but its departure starts stop 3's running
    time, 07:08:50 - 07:04:40 = 250 s. Stop 3's departure was reported only
    before its arrival, so it gives no dwell. The last stop runs 07:12:20 -
    07:09:10 = 190 s, gives no dwell and is no departure."""
    found = oracle.outcome(_quay())
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, 40), (2, 10), (3, 50)]
    assert [(t.visit.stop_sequence, t.dwell_sec, t.running_sec) for t in oracle.timings(found)] == [
        (1, 190, None),
        (2, None, None),
        (3, None, 250),
        (4, None, 190),
    ]
    assert oracle.dwell_run_by_route_day(found) == {
        ("Q", DAY, "平日"): oracle.DwellRun(dwell=(190,), running=(250, 190))
    }
    assert oracle.dwell_run_by_route(found, DAY, DAY)["Q"].running_mean_sec == 220


def test_a_departure_reported_only_before_its_arrival_must_have_been_predicted_truly():
    """Stop 3 stands through the 07:09 poll only, so its departure's last
    reading is the prediction made at its arrival's delay, +50. Leaving at
    07:09:30, 20 s later than planned, it truly left +70, so the oracle
    refuses the scenario rather than count a departure the feed misstated."""
    run = (*_QUAY_RUN[:2], ("07:08:50", "07:09:30"), _QUAY_RUN[3])
    with pytest.raises(ValueError, match="not its truth"):
        oracle.outcome(_quay(run=run))


def _late(arrival: str) -> Scenario:
    """A run two hours late into a timed hold of 20 minutes at stop 2,
    reaching that stop at *arrival* and leaving at 09:20, 6,960 s late."""
    trip = ScheduledTrip.from_clocks(
        "R_late", "R", "平日", (("07:00:00", "07:00:00"), ("07:04:00", "07:24:00"), ("07:30:00", "07:30:00"))
    )
    run = TripRun.from_clocks(trip, DAY, (("09:00:00", "09:00:00"), (arrival, "09:20:00"), ("09:26:00", "09:26:00")))
    return Scenario(
        name="late",
        routes=(_ROUTE,),
        timetable=(trip,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(run,),
        feed=FeedModel(arrivals=True),
    )


def test_an_arrival_past_the_clamp_is_dropped_so_its_stop_has_neither_dwell_nor_running_time():
    """Reached 7,200 s late, stop 2 keeps its arrival: a dwell of 960 s and a
    run of 240 s from stop 1. Reached 7,201 s late it keeps neither, though
    its departure still starts the last stop's 360 s run. Its departure,
    predicted at the arrival's delay until the run reaches it, is an
    implausible reading in each of the 130 polls from 06:55 to 09:04; the
    arrival reading itself is no departure reading and counts in no total."""
    kept = oracle.outcome(_late("09:04:00"))
    assert [(t.dwell_sec, t.running_sec) for t in oracle.timings(kept)] == [(None, None), (960, 240), (None, 360)]
    dropped = oracle.outcome(_late("09:04:01"))
    assert [(t.dwell_sec, t.running_sec) for t in oracle.timings(dropped)] == [(None, None), (None, None), (None, 360)]
    assert (kept.readings[DAY].implausible, dropped.readings[DAY].implausible) == (0, 130)


def test_arrivals_the_feed_model_cannot_follow_are_refused():
    with pytest.raises(ValueError, match="stop 1 is reached at 07:01:00, after it is left at 07:00:00"):
        _quay(plan=(("07:01:00", "07:00:00"), *_QUAY_PLAN[1:]))
    with pytest.raises(ValueError, match="stop 2 is reached at 07:00:30, not after stop 1 is left at 07:00:40"):
        _quay(run=(_QUAY_RUN[0], ("07:00:30", "07:04:40"), *_QUAY_RUN[2:]))
    with pytest.raises(ValueError, match="ends on reaching its last stop"):
        _quay(run=(*_QUAY_RUN[:3], ("07:12:20", "07:13:00")))
    with pytest.raises(ValueError, match="exactly when the feed reports them"):
        _quay(feed=FeedModel())
    with pytest.raises(ValueError, match="exactly when the feed reports them"):
        _scenario(FeedModel(arrivals=True))
    freeze = Freeze(DAY, gtfs_seconds("07:00:00"), gtfs_seconds("07:05:00"))
    with pytest.raises(ValueError, match="neither misreports nor freezes"):
        _quay(feed=FeedModel(arrivals=True, freezes=(freeze,)))
    skipping = TripRun(
        "Q_0700",
        DAY,
        (gtfs_seconds("07:00:40"), None, gtfs_seconds("07:09:10"), gtfs_seconds("07:12:20")),
        arrival_sec=tuple(gtfs_seconds(a) for a, _d in _QUAY_RUN),
    )
    with pytest.raises(ValueError, match="skips no stop"):
        Scenario(**{**_quay().__dict__, "runs": (skipping,)})


def test_a_feed_that_reports_arrivals_runs_every_trip_it_schedules():
    """Its arrivals come from runs alone, so a trip it cancels or never
    reports is refused, and no arrival it sends is ever withdrawn."""
    later = ScheduledTrip("Q_0800", "Q", "平日", ("08:00:00", "08:04:30", "08:08:20", "08:12:00"))
    scenario = {**_quay().__dict__, "timetable": (*_quay().timetable, later)}
    with pytest.raises(ValueError, match="runs every trip it schedules"):
        Scenario(**{**scenario, "cancellations": (Cancellation("Q_0800", DAY),)})
    with pytest.raises(ValueError, match="runs every trip it schedules"):
        Scenario(**{**scenario, "unreported": (Unreported("Q_0800", DAY),)})


_QUICK = ScheduledTrip("R_q", "R", "平日", ("07:00:00", "07:02:00", "07:04:00"))


def _sparse_and_dense(*deliveries: Delivery) -> Scenario:
    """One trip run to the same truth on DAY and NEXT, polled each minute and
    pushed every fifth. It enters the feed at 06:58 and leaves its stops at
    07:00:00, 07:02:30 and 07:05:00, so the 07:00 push is the only one it is
    in, and lists stops 2 and 3. Stop 3 is predicted 400 s late until 07:01."""
    return Scenario(
        name="paths",
        routes=(_ROUTE,),
        timetable=(_QUICK,),
        days=(ServiceDay(DAY, "平日"), ServiceDay(NEXT, "平日")),
        runs=tuple(TripRun.from_delays(_QUICK, day, (0, 30, 60)) for day in (DAY, NEXT)),
        feed=FeedModel(
            poll_interval_sec=60,
            lead_sec=120,
            push_interval_sec=300,
            misreports=tuple(
                Misreport("R_q", day, 400, gtfs_seconds("06:55:00"), gtfs_seconds("07:01:00"), (3,))
                for day in (DAY, NEXT)
            ),
        ),
        deliveries=deliveries,
    )


def test_the_collector_archives_every_poll_and_pushes_one_per_push_interval():
    scenario = _sparse_and_dense(Delivery(DAY, archive=True, archive_first=True), Delivery(NEXT, archive=True))
    captured = polls(scenario)
    steps = handoff(scenario, captured)
    assert [p.captured_at for p in steps.pushed] == [
        service_day_instant(day, gtfs_seconds("07:00:00")) for day in (DAY, NEXT)
    ]
    assert steps.archived_first == tuple(p for p in captured if capture_day(p) == DAY)
    assert steps.archived_after == tuple(p for p in captured if capture_day(p) == NEXT)
    assert len(steps.archived_first) == len(steps.archived_after) == 7


@pytest.mark.parametrize(
    ("archive_first", "held"), [(True, oracle.Sources(archive_rows=14, live_rows=0)), (False, oracle.Sources(0, 2))]
)
def test_a_day_offered_both_ways_is_held_by_the_path_that_stores_a_row_of_it_first(archive_first, held):
    """Archived first, DAY keeps its seven polls' 14 rows and promotion is
    left nothing; promoted first, it keeps the 07:00 push's 2 rows and its
    archive is left out. NEXT, archived alone, keeps every row."""
    scenario = _sparse_and_dense(
        Delivery(DAY, archive=True, archive_first=archive_first), Delivery(NEXT, live=False, archive=True)
    )
    found = oracle.outcome(scenario)
    assert found.sources == {DAY: held, NEXT: oracle.Sources(archive_rows=14, live_rows=0)}
    assert {(capture_day(r.poll), r.path) for r in oracle.received(scenario, polls(scenario))} == {
        (DAY, "archive" if archive_first else "live"),
        (NEXT, "archive"),
    }


def test_on_a_day_the_live_path_holds_a_departure_no_push_caught_is_missing_and_a_stale_prediction_stands():
    """DAY comes live: no push lists stop 1, and the 07:00 push, the last to
    list stop 3, predicts it 400 s late. NEXT comes from the archive, whose
    07:04 poll reads stop 3 at its true 60 s."""
    found = oracle.outcome(_sparse_and_dense(Delivery(NEXT, live=False, archive=True)))
    assert [(d.service_date, d.stop_sequence, d.delay_sec) for d in found.departures] == [
        (DAY, 2, 30),
        (DAY, 3, 400),
        (NEXT, 1, 0),
        (NEXT, 2, 30),
        (NEXT, 3, 60),
    ]
    assert oracle.by_day(found) == {
        DAY: oracle.Figures(
            departures=2,
            delay_sum_sec=430,
            on_time=1,
            over_five_min=1,
            worst_delay_sec=400,
            trip_runs=1,
            early=0,
            on_time_industry=1,
        ),
        NEXT: oracle.Figures(
            departures=3,
            delay_sum_sec=90,
            on_time=3,
            over_five_min=0,
            worst_delay_sec=60,
            trip_runs=1,
            early=0,
            on_time_industry=3,
        ),
    }
    assert found.readings == {DAY: oracle.Readings(2, 0, 2), NEXT: oracle.Readings(14, 0, 14)}
    assert found.sources == {DAY: oracle.Sources(0, 2), NEXT: oracle.Sources(14, 0)}
    assert len(found.ages) == 1 + 7


def test_a_service_day_past_midnight_holds_rows_of_both_paths_and_takes_the_source_of_most():
    """The run leaves at 23:58, 24:01 and 24:04. DAY, archived alone, keeps
    the four polls from 23:56 to 23:59, 10 rows; NEXT comes live, so the
    00:00 push brings 2 rows of DAY's service and its other polls none."""
    night = ScheduledTrip("R_2358", "R", "平日", ("23:58:00", "24:01:00", "24:04:00"))
    scenario = Scenario(
        name="midnight",
        routes=(_ROUTE,),
        timetable=(night,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(night, DAY, (0, 0, 0)),),
        feed=FeedModel(poll_interval_sec=60, lead_sec=120, push_interval_sec=300),
        deliveries=(Delivery(DAY, live=False, archive=True),),
    )
    found = oracle.outcome(scenario)
    assert found.sources == {DAY: oracle.Sources(archive_rows=10, live_rows=2)}
    assert found.sources[DAY].source == "archive"
    assert [d.delay_sec for d in found.departures] == [0, 0, 0]


def test_a_tie_reads_live_and_a_day_without_rows_has_no_source():
    assert oracle.Sources(archive_rows=2, live_rows=2).source == "live"
    assert oracle.Sources(archive_rows=3, live_rows=2).source == "archive"
    assert oracle.Sources(archive_rows=0, live_rows=0).source is None
    found = replace(
        oracle.outcome(_sparse_and_dense()), sources={DAY: oracle.Sources(2, 2), NEXT: oracle.Sources(3, 2)}
    )
    before, after = DAY - timedelta(days=1), NEXT + timedelta(days=1)
    assert oracle.source_mix(found, before, after) == oracle.SourceMix(archive_days=1, live_days=1, none_days=2)


def test_deliveries_the_oracle_cannot_follow_are_refused():
    scenario = _sparse_and_dense()
    fields = scenario.__dict__
    with pytest.raises(ValueError, match="not a multiple"):
        Scenario(**{**fields, "feed": replace(scenario.feed, push_interval_sec=90)})
    for interval in (0, -300):
        with pytest.raises(ValueError, match="must be positive"):
            Scenario(**{**fields, "feed": replace(scenario.feed, push_interval_sec=interval)})
    with pytest.raises(ValueError, match="by no path"):
        _sparse_and_dense(Delivery(DAY, live=False))
    with pytest.raises(ValueError, match="offered both ways"):
        _sparse_and_dense(Delivery(DAY, live=False, archive=True, archive_first=True))
    with pytest.raises(ValueError, match="declared twice"):
        _sparse_and_dense(Delivery(DAY), Delivery(DAY, archive=True))
    later = _sparse_and_dense(Delivery(NEXT + timedelta(days=1), archive=True))
    with pytest.raises(ValueError, match="no poll is captured"):
        handoff(later, polls(later))
    freeze = Freeze(DAY, gtfs_seconds("06:58:00"), gtfs_seconds("07:01:00"))
    with pytest.raises(ValueError, match="freezes no feed"):
        Scenario(**{**fields, "feed": FeedModel(freezes=(freeze,)), "deliveries": (Delivery(DAY, archive=True),)})
    with pytest.raises(ValueError, match="runs one timetable"):
        Scenario(**{**_revised().__dict__, "deliveries": (Delivery(DAY),)})
    sparse = FeedModel(poll_interval_sec=60, lead_sec=300, push_interval_sec=300)
    with pytest.raises(ValueError, match="neither cancels a trip nor skips a stop"):
        Scenario(**{**_skipping(None).__dict__, "feed": sparse})
    with pytest.raises(ValueError, match="neither cancels a trip nor skips a stop"):
        Scenario(**{**_cancelling(Cancellation("R_0700", DAY)).__dict__, "feed": sparse})
    Scenario(**{**_skipping(None).__dict__, "feed": replace(sparse, push_interval_sec=60)})


# Each poll's SHA-256 by its file name, per scenario of the perfect model,
# as the generator made them at commit 84a616cf, which the file records: a
# feed model added beside the perfect one must not change a byte it sends.
_PERFECT_WIRE = Path(__file__).parents[1] / "fixtures" / "perfect_wire_digests.json"


def test_the_perfect_models_wire_bytes_are_pinned_poll_by_poll():
    """Every kind of entry the perfect model sends: runs either side of
    midnight, a skip and a cancellation each predicted then announced,
    frozen and advancing headers, arrivals, sparse pushes over a misreport
    that ends, unreported trips, and misreports beyond the clamp, a stop's
    and a whole run's. A changed, added or missing poll is named by file."""
    announced = Cancellation("R_0700", DAY, announced_sec=gtfs_seconds("06:57:00"), predicted_delay_sec=30)
    scenarios = {
        "both trips": _scenario(),
        "skip announced": _skipping(gtfs_seconds("06:58:00")),
        "cancellation announced": _cancelling(announced),
        "frozen header": _frozen("frozen"),
        "advancing header": _frozen("advancing"),
        "arrivals": _quay(),
        "sparse pushes": _sparse_and_dense(),
        "bunched": _bunched(),
        "misreported": _misreporting(),
    }
    pinned = json.loads(_PERFECT_WIRE.read_text())["scenarios"]
    assert set(pinned) == set(scenarios)
    for name, scenario in scenarios.items():
        made = {poll.source_file: hashlib.sha256(poll.payload).hexdigest() for poll in polls(scenario)}
        changed = sorted(f for f in made.keys() | pinned[name].keys() if made.get(f) != pinned[name].get(f))
        assert not changed, f"{name}: these polls differ from the pinned bytes: {changed}"


_PROPAGATING = FeedModel(prediction="propagate_current_delay", poll_interval_sec=60, lead_sec=300)


def _listings(scenario_polls) -> list[tuple[str, int, tuple[tuple[int, int | None], ...]]]:
    """The polls as runs of identical listings: each run's first capture on
    the JST clock, how many polls it lasts, and the (stop, delay) it lists."""
    out: list[tuple[str, int, tuple[tuple[int, int | None], ...]]] = []
    for poll in scenario_polls:
        listing = tuple((s.stop_sequence, s.departure_delay) for t in poll.trips for s in t.stops)
        if out and out[-1][2] == listing:
            out[-1] = (out[-1][0], out[-1][1] + 1, listing)
        else:
            out.append((f"{poll.captured_at.astimezone(JST):%H:%M:%S}", 1, listing))
    return out


def test_a_propagating_feed_predicts_every_stop_at_the_delay_of_the_last_stop_left():
    """The run leaves at 06:59, 07:08 and 07:17, each on a poll, which knows
    the stop as left: the 06:59 poll lists stop 1 no longer and predicts the
    others 60 s early. So each stop is last predicted at the delay of the
    stop before it, and the first on time."""
    scenario = _one_run((-60, 0, 60), _PROPAGATING)
    assert _listings(polls(scenario)) == [
        ("06:54:00", 5, ((1, 0), (2, 0), (3, 0))),
        ("06:59:00", 9, ((2, -60), (3, -60))),
        ("07:08:00", 9, ((3, 0),)),
    ]
    found = oracle.outcome(scenario)
    assert [(d.stop_sequence, d.delay_sec) for d in found.departures] == [(1, 0), (2, -60), (3, 0)]
    assert [(p.true_delay_sec, p.error_sec, p.lead_sec) for p in found.predictions] == [
        (-60, 60, 60),
        (0, -60, 60),
        (60, -60, 60),
    ]
    assert found.readings == {DAY: oracle.Readings(readings=42, implausible=0, rows=42)}


def _quick(feed: FeedModel) -> Scenario:
    """A trip of stops two minutes apart that leaves them at 07:01:40,
    07:02:30 and 07:04:30, the first two 50 s apart."""
    return Scenario(
        name="quick",
        routes=(_ROUTE,),
        timetable=(_QUICK,),
        days=(ServiceDay(DAY, "平日"),),
        runs=(TripRun.from_delays(_QUICK, DAY, (100, 30, 30)),),
        feed=feed,
    )


def test_a_lag_decides_which_stop_a_propagated_prediction_comes_from_and_may_be_read_after_the_run_left():
    """Without a lag the 07:02 poll, the last to list stop 2, knows the run
    has left stop 1 100 s late. Half a minute behind, it knows the run as it
    stood at 07:01:30, still at stop 1, and predicts stop 2 on time; it also
    still lists stop 1, 20 s after the run left it. The next poll knows the
    run as it stood at 07:02:30, the instant it left stop 2, so no poll
    predicts 100 s, and stop 3 is predicted at stop 2's 30 s either way. The
    header shows no lag."""
    fresh = oracle.outcome(_quick(_PROPAGATING))
    lagging = _quick(replace(_PROPAGATING, freshness_lag_sec=30))
    stale = oracle.outcome(lagging)
    assert [d.delay_sec for d in fresh.departures] == [0, 100, 30]
    assert [d.delay_sec for d in stale.departures] == [0, 0, 30]
    assert [(p.error_sec, p.lead_sec) for p in fresh.predictions] == [(-100, 40), (70, 30), (0, 30)]
    assert [(p.error_sec, p.lead_sec) for p in stale.predictions] == [(-100, -20), (-30, 30), (0, 30)]
    assert _listings(polls(lagging)) == [
        ("06:56:00", 7, ((1, 0), (2, 0), (3, 0))),
        ("07:03:00", 2, ((3, 30),)),
    ]
    assert {age for _day, age in stale.ages} == {0}


def test_a_lagging_perfect_feed_keeps_every_true_delay_and_reads_some_after_the_run_left():
    """Each stop is read last in the poll before its departure plus the lag:
    06:59:00 for a departure at 06:58:59, a second after the run left."""
    fresh = oracle.outcome(_scenario())
    stale = oracle.outcome(_scenario(FeedModel(poll_interval_sec=60, lead_sec=300, freshness_lag_sec=30)))
    assert stale.departures == fresh.departures
    assert {p.error_sec for p in fresh.predictions} == {p.error_sec for p in stale.predictions} == {0}
    assert [p.lead_sec for p in fresh.predictions] == [59, 60, 1, 60, 1, 60]
    assert [p.lead_sec for p in stale.predictions] == [-1, 0, 1, 0, 1, 0]
    assert {age for _day, age in stale.ages} == {0}


def test_the_error_figure_takes_mae_and_bias_by_lead_time():
    """The lagging quick trip is reported 100 s early at stop 1, read 20 s
    after it left, 30 s early at stop 2 and truly at stop 3, both read 30 s
    ahead. A perfect feed errs only where a misreport stands."""
    found = oracle.outcome(_quick(replace(_PROPAGATING, freshness_lag_sec=30)))
    overall = oracle.prediction_error(found.predictions)
    assert overall == oracle.PredictionError(departures=3, error_sum_sec=-130, abs_error_sum_sec=130)
    assert (overall.mae_sec, overall.bias_sec) == (Fraction(130, 3), Fraction(-130, 3))
    assert oracle.prediction_error_by_lead_time(found, 30) == {
        -30: oracle.PredictionError(1, -100, 100),
        30: oracle.PredictionError(2, -30, 30),
    }
    assert oracle.prediction_error_by_lead_time(found, 60) == {
        -60: oracle.PredictionError(1, -100, 100),
        0: oracle.PredictionError(2, -30, 30),
    }
    late = Misreport("R_0700", DAY, 7200, gtfs_seconds("07:10:00"), gtfs_seconds("30:00:00"), (3,))
    misreported = oracle.outcome(_one_run((0, 0, 0), FeedModel(poll_interval_sec=60, lead_sec=300, misreports=(late,))))
    assert [(p.error_sec, p.lead_sec) for p in misreported.predictions] == [(0, 60), (0, 60), (7200, 60)]
    with pytest.raises(ValueError, match="no departures"):
        oracle.prediction_error(())
    with pytest.raises(ValueError, match="wider than nothing"):
        oracle.prediction_error_by_lead_time(found, 0)


def test_the_oracle_refuses_polls_its_feed_model_would_not_send():
    """A planted expectation of the wrong model is caught: polls a
    propagating feed sent half a minute behind, judged as a feed without the
    lag or as a perfect one, and a lagging perfect feed's polls judged under
    a longer lag, whose stops would be listed a poll longer."""
    lagging = _quick(replace(_PROPAGATING, freshness_lag_sec=30))
    captured = polls(lagging)
    for told in (replace(lagging.feed, freshness_lag_sec=0), replace(lagging.feed, prediction="perfect")):
        with pytest.raises(ValueError, match="not the model's"):
            oracle.departures(replace(lagging, feed=told), captured, captured)
    perfect = _quick(FeedModel(poll_interval_sec=60, lead_sec=300, freshness_lag_sec=30))
    captured = polls(perfect)
    with pytest.raises(ValueError, match="last read at"):
        oracle.departures(replace(perfect, feed=replace(perfect.feed, freshness_lag_sec=60)), captured, captured)


def test_imperfect_feeds_the_oracle_cannot_follow_are_refused():
    stretch = (gtfs_seconds("06:57:00"), gtfs_seconds("07:03:00"))
    for feed in (_PROPAGATING, FeedModel(freshness_lag_sec=30)):
        with pytest.raises(ValueError, match="neither reports arrivals"):
            _quay(feed=replace(feed, arrivals=True))
        with pytest.raises(ValueError, match="neither reports arrivals"):
            _one_run((0, 0, 0), replace(feed, misreports=(Misreport("R_0700", DAY, 600, *stretch),)))
        with pytest.raises(ValueError, match="neither reports arrivals"):
            _one_run((0, 0, 0), replace(feed, freezes=(Freeze(DAY, *stretch),)))
        with pytest.raises(ValueError, match="neither cancels a trip nor skips a stop"):
            Scenario(**{**_skipping(None).__dict__, "feed": feed})
        with pytest.raises(ValueError, match="neither cancels a trip nor skips a stop"):
            Scenario(**{**_cancelling(Cancellation("R_0700", DAY)).__dict__, "feed": feed})
    with pytest.raises(ValueError, match="cannot be negative"):
        _one_run((0, 0, 0), FeedModel(freshness_lag_sec=-30))


def test_the_imperfect_feed_scenario_keeps_each_stop_at_the_delay_the_run_had_half_a_minute_before_its_last_poll():
    """The runner's imperfect_feed scenario, stop by stop: the delays the
    feed last predicted, the true ones, and each kept reading's lead. Its
    07:40 run's second stop is kept on time, where without the lag it would
    be kept 100 s late."""
    scenario = imperfect_feed(DAY)
    found = oracle.outcome(scenario)
    by_run: dict[str, list[tuple[int, int, int]]] = {}
    for p in found.predictions:
        by_run.setdefault(p.departure.trip_id, []).append((p.departure.delay_sec, p.true_delay_sec, p.lead_sec))
    kept, truth, leads = ({trip: [r[k] for r in rows] for trip, rows in by_run.items()} for k in range(3))
    assert kept == {
        "P1_0700": [0, 0, 40, 80, 120, 180, 240, 301],
        "P1_0710": [0, 300, 250, 200, 150, 100, 61, 30],
        "P1_0720": [0, -90, -60, -30, 0, 0, 0, 0],
        "P1_0730": [0, 0, 0, 0, 0, 0, 0, 0],
        "P1_0740": [0, 0, 30, 30, 30, 30, 30, 30],
    }
    assert truth == {
        "P1_0700": [0, 40, 80, 120, 180, 240, 301, 361],
        "P1_0710": [300, 250, 200, 150, 100, 61, 30, -30],
        "P1_0720": [-90, -60, -30, 0, 0, 0, 0, 0],
        "P1_0730": [0, 0, 0, 0, 0, 0, 0, 0],
        "P1_0740": [100, 30, 30, 30, 30, 30, 30, 30],
    }
    assert leads == {
        "P1_0700": [0, -20, 20, 0, 0, 0, 1, 1],
        "P1_0710": [0, 10, 20, 30, -20, 1, 30, 30],
        "P1_0720": [30, 0, 30, 0, 0, 0, 0, 0],
        "P1_0730": [0, 0, 0, 0, 0, 0, 0, 0],
        "P1_0740": [-20, 30, 30, 30, 30, 30, 30, 30],
    }
    fresh = oracle.outcome(replace(scenario, feed=replace(scenario.feed, freshness_lag_sec=0)))
    assert [d.delay_sec for d in fresh.departures if d.trip_id == "P1_0740"][:2] == [0, 100]
    assert {age for _day, age in found.ages} == {0}


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _positions_on_the_wire(payload: bytes):
    """A VehiclePositions feed as decoded from its bytes: its header's
    timestamp, and per entity its id, the fields its VehiclePosition
    carries, and their values: (trip_id, start_date, latitude, longitude,
    timestamp, vehicle id)."""
    message = _fields(payload)
    (header,) = message[1]
    entities = []
    for raw in message.get(2, []):
        entity = _fields(raw)
        (position,) = entity[4]
        vp = _fields(position)
        trip, where, vehicle = _fields(vp[1][0]), _fields(vp[2][0]), _fields(vp[8][0])
        latitude, longitude = (struct.unpack("<f", struct.pack("<I", where[k][0]))[0] for k in (1, 2))
        entities.append(
            (
                entity[1][0].decode(),
                sorted(vp),
                (trip[1][0].decode(), trip[3][0].decode(), latitude, longitude, vp[5][0], vehicle[1][0].decode()),
            )
        )
    return _fields(header)[3][0], entities


def test_vehicle_positions_round_trip_and_pass_each_stop_at_the_truths_times(tmp_path):
    """The run is sampled every 20 s from 06:54, when it enters the feed, to
    07:18:00, when it reaches its last stop, and leaves its stops at 06:59:00,
    07:08:40 and 07:18:00, each on a sample. The collector names each file
    by its UTC day and time, the previous day's for a JST morning. Each
    position names its trip, service date, vehicle and measuring instant,
    and no stop or status."""
    scenario = _one_run((-60, 40, 120))
    (run,) = scenario.runs
    written = write_vehicle_positions(scenario, tmp_path, 8, 20)
    assert len(written) == 73
    assert written[0] == tmp_path / "vp" / "8" / "20260531" / "VehiclePosition_215400.pb"
    assert written[-1] == tmp_path / "vp" / "8" / "20260531" / "VehiclePosition_221800.pb"
    seen = {}
    for path, poll in zip(written, vehicle_positions(scenario, 20), strict=True):
        stamped, entities = _positions_on_the_wire(path.read_bytes())
        assert stamped == int(poll.captured_at.timestamp())
        ((entity_id, fields, (trip_id, start_date, lat, lon, measured, vehicle)),) = entities
        assert (entity_id, vehicle, trip_id, start_date) == (vehicle_id(run), vehicle_id(run), "R_0700", "20260601")
        assert fields == [1, 2, 5, 8]
        assert measured == stamped
        seen[poll.captured_at.astimezone(JST).strftime("%H:%M:%S")] = (lat, lon)
    s1, s2, s3 = ((_float32(s.lat), _float32(s.lon)) for s in _STOPS)
    assert {seen["06:54:00"], seen["06:59:00"]} == {s1}
    assert seen["07:08:40"] == s2
    assert seen["07:18:00"] == s3
    # 240 s into the 580 s from stop 1 to stop 2.
    share = Fraction(240, 580)
    expected = (_STOPS[0].lat + (_STOPS[1].lat - _STOPS[0].lat) * share, _STOPS[0].lon)
    assert seen["07:03:00"] == pytest.approx(expected, abs=2e-5)
    assert seen["07:03:00"] != s1


def test_a_vehicle_stands_at_a_stop_through_its_dwell_and_passes_a_skipped_stop_without_stopping():
    """The quay run stands at stop 2 from 07:04:10 to 07:04:40. The skipping
    run leaves stop 1 at 07:00:30 and stop 3 at 07:17:30, passing stop 2,
    halfway along, at 07:09:00. A trip that did not run, cancelled or never
    reported, has no vehicle."""
    quay = {
        p.captured_at.astimezone(JST).strftime("%H:%M:%S"): (s.lat, s.lon)
        for p in vehicle_positions(_quay(), 10)
        for s in p.sightings
    }
    q2 = (_QUAYS[1].lat, _QUAYS[1].lon)
    assert [quay[f"07:04:{sec}"] for sec in ("10", "20", "30", "40")] == [q2] * 4
    assert quay["07:04:00"] != q2 != quay["07:04:50"]
    assert quay["06:55:00"] == quay["07:00:40"] == (_QUAYS[0].lat, _QUAYS[0].lon)
    skipping = {
        p.captured_at.astimezone(JST).strftime("%H:%M:%S"): (s.lat, s.lon)
        for p in vehicle_positions(_skipping(None), 30)
        for s in p.sightings
    }
    assert skipping["07:09:00"] == pytest.approx((_STOPS[1].lat, _STOPS[1].lon), abs=1e-9)
    assert skipping["07:17:30"] == (_STOPS[2].lat, _STOPS[2].lon)
    cancelled = vehicle_positions(_cancelling(Cancellation("R_0700", DAY)), 60)
    assert {s.trip_id for p in cancelled for s in p.sightings} == {"R_2350"}
    unreported = vehicle_positions(_bunched(), 60)
    assert {s.trip_id for p in unreported for s in p.sightings} == {"R_0", "R_1", "R_3"}
