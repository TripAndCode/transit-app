"""The scenario generator and its oracle, checked by hand without a database."""

import csv
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

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
    FeedModel,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    TimetableVersion,
    TripRun,
    Unreported,
    gtfs_seconds,
    poll_batches,
    polls,
    service_day_instant,
    static_gtfs_zip,
)

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
    assert oracle.by_day(oracle.outcome(_scenario())) == {
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
    figures = oracle.by_route(oracle.outcome(_scenario()))["R"]
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
    assert {
        (d.service_date, d.trip_id, d.scheduled_sec) for d in oracle.departures(scenario) if d.stop_sequence == 1
    } == {
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
    assert [(d.stop_sequence, d.delay_sec) for d in oracle.departures(scenario)] == [(1, 30), (3, 90)]
    # A run that skipped a stop still ran: executed, and observed.
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=1, reported_not_run=0, observed=1)}


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
    assert [(d.stop_sequence, d.delay_sec) for d in oracle.departures(scenario)] == [(2, 40), (3, 90)]


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
    assert {d.trip_id for d in oracle.departures(scenario)} == {"R_2350"}
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}
    assert oracle.service(scenario)[DAY].executed == 1
    assert oracle.delivered_rate_shown(oracle.outcome(scenario), DAY)


def test_a_cancellation_may_list_no_stop_at_all():
    scenario = _cancelling(Cancellation("R_0700", DAY, reported_as="canceled_without_stops"))
    morning = [trip for _at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    assert all(trip_status == CANCELED and stops == [] for _trip_id, trip_status, stops in morning)
    assert all(p.stop_updates == len(list(_decode_rows(p.payload))) for p in polls(scenario))
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


def test_a_trip_reported_with_every_stop_skipped_did_not_run():
    scenario = _cancelling(Cancellation("R_0700", DAY, reported_as="every_stop_skipped"))
    morning = [trip for _at, trips in _statuses(polls(scenario)) for trip in trips if trip[0] == "R_0700"]
    assert morning
    for _trip_id, trip_status, stops in morning:
        assert trip_status == SCHEDULED
        assert stops
        assert {(delay, status) for _seq, delay, status in stops} == {(None, SKIPPED)}
    assert {d.trip_id for d in oracle.departures(scenario)} == {"R_2350"}
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


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
    assert {d.trip_id for d in oracle.departures(scenario)} == {"R_2350"}
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=2, reported_not_run=1, observed=1)}


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
    assert oracle.service(scenario) == {DAY: oracle.Service(planned=2, reported_not_run=0, observed=1)}
    assert oracle.service(scenario)[DAY].executed == 2
    assert not oracle.delivered_rate_shown(oracle.outcome(scenario), DAY)
