"""Scenarios with known truth, pushed through the real ingest path.

Each scenario (``tests.fixtures.scenario``) declares an operation and its
truth. Its static GTFS loads through ``load_static``. Each TripUpdate poll the
collector pushes is POSTed to the collector's ingest endpoint, and promotion
moves the closed days into ``updates``; each poll of a day the scenario
delivers by the archive is written as the collector's archive file and read
by archive ingest, before or after promotion as the scenario says.
``analyze`` builds the aggregates after each step. The aggregates, the
report endpoints, the agencies board and the feed-quality scorecard are
compared with ``tests.fixtures.scenario_oracle``, which computes the same
figures from the truth and the polls of its feed that reach ``updates``.
A feed that predicts imperfectly is held to the delays it last predicted,
which the oracle derives from its feed model, not to the true ones.

A scenario whose timetable is revised loads each version at the instant it
declares, as an operator loads a newly published zip, and ingests each poll
against the version loaded when it was captured. After each version's polls
the runner promotes and analyzes, as the daily jobs would before the next
version arrives, so the run that sees the new version updates aggregates
built under the old one. A scenario that delivers a day by the archive runs
one timetable.

The service days sit from two days before the service day in progress, and
every poll must land on a closed JST day; every pushed poll must also outlast
the run in ``updates_live`` (``tests.fixtures.scenario_clock``), so a day
delivered by the archive alone may sit earlier. Day types are declared with
each day, not read from its weekday. A table keyed on the day of the week is
compared under the service date's own weekday, which the oracle reads from
the same date.

Each scenario runs through the pipeline once per module (the ``observed``
fixture), and every test compares that one snapshot with the oracle. Every
scenario's feed stands for one whose status and arrival fields a probe has
confirmed: the runner records that verdict by hand, since most scenario
feeds carry no arrival times and a live probe of them would refute the
arrival field. Their dwell and running times are compared all the same, as
the samples none of their visits pair.
"""

from __future__ import annotations

import contextlib
import itertools
import os
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from functools import cached_property, partial
from pathlib import Path
from typing import Any

import clickhouse_connect
import httpx
import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport

from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from tests.fixtures import scenario_oracle as oracle
from tests.fixtures.scenario import (
    JST,
    SCHEDULED,
    Cancellation,
    Delivery,
    FeedModel,
    Freeze,
    Misreport,
    Poll,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    TimetableVersion,
    TripRun,
    Unreported,
    gtfs_clock,
    gtfs_seconds,
    handoff,
    poll_batches,
    static_gtfs_zip,
)
from tests.fixtures.scenario_clock import anchor_day, check_closed, check_closed_and_live

_COLLECTOR_SECRET = "scenario-secret"


def _route(route_id: str, name: str, n_stops: int, lat: float) -> Route:
    stops = tuple(
        Stop(f"{route_id}_S{i}", f"{name} {i}", round(lat + i * 0.002, 6), round(132.45 + i * 0.002, 6))
        for i in range(1, n_stops + 1)
    )
    return Route(route_id, name, stops)


def _trip(route: Route, day_type: str, first: str, spacing_sec: int, trip_id: str | None = None) -> ScheduledTrip:
    start = gtfs_seconds(first)
    departures = tuple(gtfs_clock(start + i * spacing_sec) for i in range(len(route.stops)))
    return ScheduledTrip(
        trip_id or f"{route.route_id}_{first[:5].replace(':', '')}", route.route_id, day_type, departures
    )


def _runs(route: Route, day_type: str, day: date, spacing_sec: int, delays: Mapping[str, tuple[int | None, ...]]):
    trips = [_trip(route, day_type, first, spacing_sec) for first in delays]
    runs = [TripRun.from_delays(trip, day, d) for trip, d in zip(trips, delays.values(), strict=True)]
    return trips, runs


# Delays in seconds, per stop. The mix spans early by more than a minute,
# on time, late, and over five minutes late, and sits on both sides of each
# rule's edge: 60 and 61, 300 and 301.
_WEEKDAY = {
    "07:00:00": (-90, -60, -30, 0, 0, 30, 45, 60),
    "07:10:00": (0, 30, 60, 61, 90, 120, 180, 240),
    "07:20:00": (120, 200, 300, 301, 360, 420, 480, 420),
    "07:30:00": (-120, -100, -70, -30, 0, 15, 30, 20),
    "07:40:00": (30, 60, 90, 150, 240, 330, 400, 450),
    "07:50:00": (0, 0, 0, 10, 20, 30, 40, 50),
}


def plain_weekday(day: date) -> Scenario:
    """One route on one weekday: six trips of eight stops."""
    route = _route("R1", "Plain line", 8, 34.39)
    trips, runs = _runs(route, "平日", day, 120, _WEEKDAY)
    return Scenario(
        name="plain_weekday",
        routes=(route,),
        timetable=tuple(trips),
        days=(ServiceDay(day, "平日"),),
        runs=tuple(runs),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300),
    )


# The 23:50 trip crosses midnight between its fourth and fifth stops; the
# 24:10 trip runs wholly after it, on the previous day's service.
_NIGHT = {
    "23:10:00": (-90, -30, 0, 30, 60, 90, 120, 150),
    "23:30:00": (0, 60, 120, 200, 280, 320, 360, 400),
    "23:50:00": (30, 45, 61, 120, 180, 240, 301, 330),
    "24:10:00": (-120, -61, 0, 20, 40, 300, 310, 299),
}
_MORNING = {
    "06:00:00": (0, 10, 20, 30, 40, 50, 60, 70),
    "06:20:00": (-60, 0, 90, 150, 240, 360, 420, 480),
}


def night_route(day: date) -> Scenario:
    """A night route whose last trips run past midnight on *day*'s service,
    then two morning trips of the next service day under another day type.
    The after-midnight departures of *day* and the morning departures of the
    next day fall on one calendar day and must still file under two."""
    route = _route("N1", "Night line", 8, 34.41)
    night_trips, night_runs = _runs(route, "平日", day, 180, _NIGHT)
    morning_trips, morning_runs = _runs(route, "土曜", day + timedelta(days=1), 180, _MORNING)
    return Scenario(
        name="night_route",
        routes=(route,),
        timetable=(*night_trips, *morning_trips),
        days=(ServiceDay(day, "平日"), ServiceDay(day + timedelta(days=1), "土曜")),
        runs=(*night_runs, *morning_runs),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300),
    )


def timetable_change(day: date) -> Scenario:
    """One route whose timetable is revised from the next day. The revision
    keeps trip a as it was, moves trip b from 07:12 to 07:56 so that it
    crosses into hour 8 under the same trip_id, drops trip c, and adds
    trips d and e. It is loaded overnight, before the next day's first poll."""
    route = _route("T1", "Revised line", 6, 34.43)
    revised_day = day + timedelta(days=1)
    a = _trip(route, "平日", "07:00:00", 120, "T1_a")
    b_before = _trip(route, "平日", "07:12:00", 120, "T1_b")
    c = _trip(route, "平日", "07:24:00", 120, "T1_c")
    b_after = _trip(route, "平日", "07:56:00", 120, "T1_b")
    d = _trip(route, "平日", "08:00:00", 120, "T1_d")
    e = _trip(route, "平日", "08:04:00", 120, "T1_e")
    return Scenario(
        name="timetable_change",
        routes=(route,),
        timetable=(a, b_before, c),
        revisions=(TimetableVersion("scenario_timetable_change_revised", revised_day, (a, b_after, d, e)),),
        days=(ServiceDay(day, "平日"), ServiceDay(revised_day, "平日")),
        runs=(
            TripRun.from_delays(a, day, (0, 30, 60, 90, 120, 150)),
            TripRun.from_delays(b_before, day, (-70, -30, 0, 61, 120, 301)),
            TripRun.from_delays(c, day, (20, 40, 60, 80, 100, 120)),
            TripRun.from_delays(a, revised_day, (10, 20, 30, 40, 50, 60)),
            TripRun.from_delays(b_after, revised_day, (300, 301, 240, 200, 120, 90)),
            TripRun.from_delays(d, revised_day, (-90, -61, -60, 0, 60, 61)),
            TripRun.from_delays(e, revised_day, (0, 0, 15, 30, 45, 60)),
        ),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300),
    )


def timetable_published_early(day: date) -> Scenario:
    """A revision of the same kind as ``timetable_change``, published and
    loaded at 10:00 on the day before it takes effect, between that day's
    morning trip and its afternoon trips. The revision moves trip b from
    12:30 to 12:52, drops trip c and adds trip d."""
    route = _route("E1", "Early-published line", 6, 34.47)
    revised_day = day + timedelta(days=1)
    a = _trip(route, "平日", "07:00:00", 120, "E1_a")
    b_before = _trip(route, "平日", "12:30:00", 120, "E1_b")
    c = _trip(route, "平日", "12:42:00", 120, "E1_c")
    b_after = _trip(route, "平日", "12:52:00", 120, "E1_b")
    d = _trip(route, "平日", "13:00:00", 120, "E1_d")
    revision = TimetableVersion(
        "scenario_timetable_published_early_revised",
        revised_day,
        (a, b_after, d),
        loaded_at=datetime.combine(day, time(10), tzinfo=JST),
    )
    return Scenario(
        name="timetable_published_early",
        routes=(route,),
        timetable=(a, b_before, c),
        revisions=(revision,),
        days=(ServiceDay(day, "平日"), ServiceDay(revised_day, "平日")),
        runs=(
            TripRun.from_delays(a, day, (0, 30, 60, 90, 120, 150)),
            TripRun.from_delays(b_before, day, (-70, -30, 0, 61, 120, 301)),
            TripRun.from_delays(c, day, (20, 40, 60, 80, 100, 120)),
            TripRun.from_delays(a, revised_day, (10, 20, 30, 40, 50, 60)),
            TripRun.from_delays(b_after, revised_day, (300, 301, 240, 200, 120, 90)),
            TripRun.from_delays(d, revised_day, (-90, -61, -60, 0, 60, 61)),
        ),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300),
    )


# The cancellations scenario's routes, by what each carries besides ordinary
# runs. Each route has its own stops, and the routes whose predicted
# departures a later report withdraws own their scheduled hours, so the cells
# a mistake there would move hold nothing else.
_CONTROL = "C0"
_CANCELLED_LISTING_STOPS = "CL"
_CANCELLED_WITHOUT_STOPS = "CT"
_CANCELLED_AFTER_PREDICTIONS = "CC"
_UNREPORTED = "CS"
_EVERY_STOP_SKIPPED = "CA"
_SKIPPED_FROM_THE_START = "CK"
_SKIPPED_AFTER_PREDICTIONS = "CP"
_SKIPPED_THEN_NO_DATA = "CN"
_CANCELLED_WITHOUT_STOPS_AFTER_PREDICTIONS = "CW"
# Each route runs four trips of eight stops, six minutes apart, from these
# first departures, all of a group's departures inside its hour.
_CANCELLATION_TIMETABLE = {
    (_CONTROL, "平日"): "07:00:00",
    (_CANCELLED_LISTING_STOPS, "平日"): "07:01:00",
    (_CANCELLED_WITHOUT_STOPS, "平日"): "07:02:00",
    (_CANCELLED_AFTER_PREDICTIONS, "平日"): "08:00:00",
    (_CONTROL, "土曜"): "07:00:00",
    (_SKIPPED_FROM_THE_START, "土曜"): "07:01:00",
    (_EVERY_STOP_SKIPPED, "土曜"): "07:02:00",
    (_UNREPORTED, "土曜"): "07:03:00",
    (_SKIPPED_AFTER_PREDICTIONS, "土曜"): "08:00:00",
    (_SKIPPED_THEN_NO_DATA, "土曜"): "07:04:00",
    (_CANCELLED_WITHOUT_STOPS_AFTER_PREDICTIONS, "平日"): "09:00:00",
}


def _drifting_delays(seed: int, n_stops: int) -> tuple[int, ...]:
    """Delays that start between 90 s early and 2 min late and drift by
    between a minute down and 90 s up at each stop, so that a run with
    stops two minutes apart still leaves them in order. The seed spreads
    them over early, on time, late and over five minutes late."""
    delays = [(seed * 37) % 211 - 90]
    for i in range(1, n_stops):
        delays.append(delays[-1] + (seed * 13 + i * 29) % 151 - 60)
    return tuple(delays)


def cancellations(day: date) -> Scenario:
    """Trips that did not run, and runs that skipped stops.

    On the first day, a 平日, routes CL and CT each lose their last trip,
    cancelled with its stops listed and with none listed, and CC's last trip
    is predicted 90 s late and then cancelled two minutes before it was due.
    CW's last trip is predicted the same way and then cancelled with no stop
    listed.
    On the second day, a 土曜, CA's last trip does not run and the feed
    reports every stop of it SKIPPED, and CS's last trip does not run without
    the feed ever mentioning it. CN's last trip does not run either: the feed
    lists every stop of it SKIPPED until the stop is due and NO_DATA after.
    CK's last run skips its third stop, reported SKIPPED from the start, and
    CP's skips its third stop too, predicted until the feed announces the
    skip. Every other trip runs, C0's on both days.

    The feed is polled every two minutes."""
    second = day + timedelta(days=1)
    codes = (
        _CONTROL,
        _CANCELLED_LISTING_STOPS,
        _CANCELLED_WITHOUT_STOPS,
        _CANCELLED_AFTER_PREDICTIONS,
        _SKIPPED_FROM_THE_START,
        _EVERY_STOP_SKIPPED,
        _UNREPORTED,
        _SKIPPED_AFTER_PREDICTIONS,
        _SKIPPED_THEN_NO_DATA,
        _CANCELLED_WITHOUT_STOPS_AFTER_PREDICTIONS,
    )
    routes = {code: _route(code, f"Line {code}", 8, 34.45 + i * 0.02) for i, code in enumerate(codes)}
    trips = {
        (code, day_type): tuple(
            _trip(
                routes[code],
                day_type,
                gtfs_clock(gtfs_seconds(first) + k * 360),
                120,
                f"{code}_{'wd' if day_type == '平日' else 'sa'}{k}",
            )
            for k in range(4)
        )
        for (code, day_type), first in _CANCELLATION_TIMETABLE.items()
    }
    service_date = {"平日": day, "土曜": second}

    def last(code: str, day_type: str) -> ScheduledTrip:
        return trips[(code, day_type)][-1]

    skipping = {
        last(_SKIPPED_FROM_THE_START, "土曜").trip_id: None,
        last(_SKIPPED_AFTER_PREDICTIONS, "土曜").trip_id: gtfs_seconds("08:21:00"),
    }
    not_run = {
        *(
            last(code, "平日").trip_id
            for code in (
                _CANCELLED_LISTING_STOPS,
                _CANCELLED_WITHOUT_STOPS,
                _CANCELLED_AFTER_PREDICTIONS,
                _CANCELLED_WITHOUT_STOPS_AFTER_PREDICTIONS,
            )
        ),
        *(last(code, "土曜").trip_id for code in (_EVERY_STOP_SKIPPED, _UNREPORTED, _SKIPPED_THEN_NO_DATA)),
    }
    runs = []
    for seed, ((_code, day_type), route_trips) in enumerate(trips.items()):
        for k, trip in enumerate(route_trips):
            if trip.trip_id in not_run:
                continue
            delays: tuple[int | None, ...] = _drifting_delays(seed * 4 + k, len(trip.departures))
            if trip.trip_id in skipping:
                delays = (*delays[:2], None, *delays[3:])
            runs.append(
                TripRun.from_delays(
                    trip, service_date[day_type], delays, skips_announced_sec=skipping.get(trip.trip_id)
                )
            )
    return Scenario(
        name="cancellations",
        routes=tuple(routes.values()),
        timetable=tuple(t for route_trips in trips.values() for t in route_trips),
        days=(ServiceDay(day, "平日"), ServiceDay(second, "土曜")),
        runs=tuple(runs),
        cancellations=(
            Cancellation(last(_CANCELLED_LISTING_STOPS, "平日").trip_id, day),
            Cancellation(last(_CANCELLED_WITHOUT_STOPS, "平日").trip_id, day, reported_as="canceled_without_stops"),
            Cancellation(
                last(_CANCELLED_AFTER_PREDICTIONS, "平日").trip_id,
                day,
                announced_sec=gtfs_seconds("08:16:00"),
                predicted_delay_sec=90,
            ),
            Cancellation(
                last(_CANCELLED_WITHOUT_STOPS_AFTER_PREDICTIONS, "平日").trip_id,
                day,
                announced_sec=gtfs_seconds("09:16:00"),
                reported_as="canceled_without_stops",
                predicted_delay_sec=90,
            ),
            Cancellation(last(_EVERY_STOP_SKIPPED, "土曜").trip_id, second, reported_as="every_stop_skipped"),
            Cancellation(last(_SKIPPED_THEN_NO_DATA, "土曜").trip_id, second, reported_as="skipped_then_no_data"),
        ),
        unreported=(Unreported(last(_UNREPORTED, "土曜").trip_id, second),),
        feed=FeedModel(poll_interval_sec=120, lead_sec=300),
    )


# The bunching scenario's lines, eight stops two minutes apart. The bunching
# line runs eleven trips six minutes apart, so its long-gap edge is 1.75 x 360
# = 630 s. The second line runs three trips 12 min 1 s apart, a second over
# the high-frequency edge, so its long-gap edge, 1261.75 s, falls between two
# whole seconds.
_BUNCHING_HEADWAY_SEC = 360
_OVER_THE_EDGE_HEADWAY_SEC = 721
# Delays in seconds, per stop, of the bunching line's trips from 07:00, one
# row per trip and None for a trip that did not run. Each pair's leader falls
# further behind along the line and its follower catches it up, so the gap
# inside the pair closes while the gap to the next pair opens past the edge:
# 630 s is not long and 631 s is. On the first day the fourth trip overtakes
# the third before the last stop.
_BUNCHED = (
    (0, 30, 60, 90, 120, 150, 180, 210),
    (0, -10, -20, -30, -40, -50, -60, -70),
    (20, 60, 100, 140, 180, 220, 260, 300),
    (10, -10, -30, -50, -70, -80, -90, -100),
    (30, 60, 100, 140, 170, 191, 230, 270),
    (20, 10, 0, -10, -20, -30, -40, -40),
    (0, 20, 50, 80, 110, 140, 170, 200),
    (0, 0, 0, 0, 0, 0, 0, 0),
    (10, 40, 70, 100, 130, 160, 190, 220),
    (0, -10, -20, -30, -40, -50, -60, -60),
    (0, 30, 60, 90, 120, 150, 180, 200),
)
# The second day bunches less, but its sixth trip does not run and the feed
# never mentions it, which leaves a gap past the edge at every stop.
_GAPPED = (
    (0, 10, 20, 30, 40, 50, 60, 70),
    (0, 0, 10, 10, 20, 20, 30, 30),
    (30, 60, 90, 120, 150, 180, 210, 240),
    (0, -10, -10, -20, -20, -30, -30, -40),
    (0, 0, 0, 0, 0, 0, 0, 0),
    None,
    (-30, -30, -40, -40, -50, -50, -60, -60),
    (30, 60, 90, 120, 150, 180, 210, 240),
    (0, 0, 0, 0, 0, 0, 0, 0),
    (0, 10, 20, 30, 40, 50, 60, 70),
    (10, 25, 40, 55, 70, 85, 100, 115),
)
# The second line's three trips on each day: a gap of 1262 s at the last
# stop of the first day, and one of 1261 s there on the second.
_OVER_THE_EDGE = (
    ((0, 0, 0, 0, 0, 0, 0, 0), (0, 100, 200, 300, 400, 500, 520, 541), (0, 50, 100, 150, 200, 250, 300, 350)),
    ((0, 0, 0, 0, 0, 0, 0, 0), (0, 100, 200, 300, 400, 500, 520, 540), (0, 0, 0, 0, 0, 0, 0, 0)),
)


def _every(route: Route, first: str, n: int, headway_sec: int) -> tuple[ScheduledTrip, ...]:
    start = gtfs_seconds(first)
    return tuple(
        _trip(route, "平日", gtfs_clock(start + k * headway_sec), 120, f"{route.route_id}_{k}") for k in range(n)
    )


def bunching(day: date) -> Scenario:
    """A high-frequency line whose trips run in pairs on two weekdays, with
    a trip missing on the second, beside a line a second too sparse to be
    high-frequency. Every day runs the one timetable."""
    second = day + timedelta(days=1)
    line = _route("B1", "Bunching line", 8, 34.49)
    sparse = _route("B2", "Twelve-minute line", 8, 34.51)
    line_trips = _every(line, "07:00:00", len(_BUNCHED), _BUNCHING_HEADWAY_SEC)
    sparse_trips = _every(sparse, "07:00:00", len(_OVER_THE_EDGE[0]), _OVER_THE_EDGE_HEADWAY_SEC)
    runs, unreported = [], []
    for service_date, line_delays, sparse_delays in zip(
        (day, second), (_BUNCHED, _GAPPED), _OVER_THE_EDGE, strict=True
    ):
        for trip, delays in zip(line_trips, line_delays, strict=True):
            if delays is None:
                unreported.append(Unreported(trip.trip_id, service_date))
            else:
                runs.append(TripRun.from_delays(trip, service_date, delays))
        runs += [TripRun.from_delays(t, service_date, d) for t, d in zip(sparse_trips, sparse_delays, strict=True)]
    return Scenario(
        name="bunching",
        routes=(line, sparse),
        timetable=(*line_trips, *sparse_trips),
        days=(ServiceDay(day, "平日"), ServiceDay(second, "平日")),
        runs=tuple(runs),
        unreported=tuple(unreported),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300),
    )


# The clamp scenario's runs, by first scheduled departure: delays in seconds,
# per stop, of a line of six stops two minutes apart. The first two runs
# truly leave about two hours late and early, across the clamp's edge: 7,200 s
# either way is plausible and 7,201 s is not. The others run on time or close;
# the feed misreports the 09:20, 09:30, 09:40 and 09:50 runs
# (_clamp_misreports) and reports the 10:00 and 10:10 runs truly.
_CLAMP_TRUTH = {
    "09:00:00": (7140, 7170, 7200, 7201, 7230, 7260),
    "09:10:00": (-7260, -7230, -7201, -7200, -7170, -7140),
    "09:20:00": (0, 20, 40, 60, 80, 100),
    "09:30:00": (30, 60, 90, 60, 30, 0),
    "09:40:00": (10, 20, 30, 40, 50, 60),
    "09:50:00": (20, 20, 20, 20, 20, 20),
    "10:00:00": (-30, 0, 30, 60, 120, 180),
    "10:10:00": (0, 0, 0, 0, 0, 0),
}
# A day late and a day early, as a feed resending another day's prediction does.
_DAY_SEC = 24 * 3600


def _clamp_misreports(day: date) -> tuple[Misreport, ...]:
    """The 09:20 run is reported a day late in every poll, so no reading of
    it is plausible. The 09:30 run is reported a day early from 09:26 to
    09:29 and then truly again. The 09:40 run is reported a day early from
    09:43 until it ends, so each stop it has not left by then keeps the
    reading before. From 09:50 the 09:50 run's second to fifth stops are
    reported 7,200 s late, 7,201 s late, 7,200 s early and 7,201 s early,
    each until the run leaves it: the plausible two become those stops' last
    readings, and the other two keep their readings of 09:49."""
    clock = gtfs_seconds
    edges = ((2, 7200), (3, 7201), (4, -7200), (5, -7201))
    return (
        Misreport("K1_0920", day, _DAY_SEC, 0, clock("30:00:00")),
        Misreport("K1_0930", day, -_DAY_SEC, clock("09:26:00"), clock("09:29:00")),
        Misreport("K1_0940", day, -_DAY_SEC, clock("09:43:00"), clock("30:00:00")),
        *(Misreport("K1_0950", day, delay, clock("09:50:00"), clock("30:00:00"), (seq,)) for seq, delay in edges),
    )


def clamp(day: date) -> Scenario:
    """A ten-minute line on one weekday whose readings mix the plausible
    with readings beyond the clamp: true delays across its edge, and runs
    the feed misreports for a stretch, the whole run, or up to each stop's
    departure."""
    route = _route("K1", "Clamped line", 6, 34.53)
    trips, runs = _runs(route, "平日", day, 120, _CLAMP_TRUTH)
    return Scenario(
        name="clamp",
        routes=(route,),
        timetable=tuple(trips),
        days=(ServiceDay(day, "平日"),),
        runs=tuple(runs),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300, misreports=_clamp_misreports(day)),
    )


# The frozen feed's runs from 08:00, six minutes apart, eight stops two
# minutes apart each: delays in seconds, per stop.
_FROZEN_TRUTH = {
    "08:00:00": (0, 10, 20, 30, 40, 50, 60, 70),
    "08:06:00": (-30, -20, 0, 30, 60, 90, 120, 150),
    "08:12:00": (60, 120, 180, 240, 300, 330, 360, 400),
    "08:18:00": (0, 0, 0, 0, 0, 0, 10, 10),
    "08:24:00": (20, 40, 61, 90, 120, 180, 240, 301),
}


def frozen_feed(day: date) -> Scenario:
    """A six-minute line on one weekday whose feed stops updating twice.
    Each run enters the feed 44 minutes before it leaves, so every run is in
    it by 07:40. From then until 08:28 the feed resends the 07:40 poll, its
    header included: each poll reads a minute older than the last, and lists
    every stop at its true delay long after the run has left it. From 08:28
    to 08:34 it resends the 08:28 poll's TripUpdates under a header that
    keeps the capture time, so the stale content reads as fresh."""
    route = _route("F1", "Frozen line", 8, 34.55)
    trips, runs = _runs(route, "平日", day, 120, _FROZEN_TRUTH)
    clock = gtfs_seconds
    return Scenario(
        name="frozen_feed",
        routes=(route,),
        timetable=tuple(trips),
        days=(ServiceDay(day, "平日"),),
        runs=tuple(runs),
        feed=FeedModel(
            poll_interval_sec=60,
            lead_sec=44 * 60,
            freezes=(
                Freeze(day, clock("07:40:00"), clock("08:28:00"), "frozen"),
                Freeze(day, clock("08:28:00"), clock("08:34:00"), "advancing"),
            ),
        ),
    )


# The dwell scenario's trips: each stop's (arrival, departure), planned, then
# as each run reached and left it. The feed is polled on the minute.
_DWELL_LINE_A = (
    ("06:55:00", "07:00:00"),
    ("07:03:00", "07:03:30"),
    ("07:07:00", "07:07:40"),
    ("07:11:00", "07:11:30"),
    ("07:15:00", "07:17:00"),
    ("07:21:00", "07:21:00"),
)
_DWELL_RUN_A = (
    ("06:57:30", "07:00:20"),  # back from a layover in time: a dwell of 170 s, the first stop's
    ("07:03:40", "07:05:10"),  # 90 s
    ("07:07:30", "07:08:10"),  # standing through 07:08 only: no dwell
    ("07:11:10", "07:11:40"),  # standing through no poll: no arrival
    ("07:14:30", "07:17:05"),  # early into a timed hold: 155 s
    ("07:21:30", "07:21:30"),
)
_DWELL_LINE_B = (
    ("07:30:00", "07:30:00"),
    ("07:33:00", "07:33:30"),
    ("07:37:00", "07:37:40"),
    ("07:41:00", "07:41:30"),
    ("07:45:00", "07:47:00"),
    ("07:51:00", "07:51:00"),
)
_DWELL_RUN_B = (
    ("07:30:10", "07:30:10"),  # leaving the first stop as it reaches it: no arrival
    ("07:33:50", "07:35:30"),  # 100 s
    ("07:37:05", "07:39:55"),  # 170 s
    ("07:41:40", "07:42:10"),  # standing through 07:42 only: no dwell
    ("07:45:20", "07:47:20"),  # the planned 120 s
    ("07:50:40", "07:50:40"),
)
# Two hours late, with timed holds of 20 and 10 minutes.
_LATE_LINE = (
    ("07:30:00", "07:30:00"),
    ("07:33:50", "07:53:50"),
    ("07:58:00", "08:08:00"),
    ("08:12:00", "08:12:00"),
)
_LATE_RUN = (
    ("09:30:00", "09:30:00"),
    ("09:33:50", "09:48:50"),  # reached 7,200 s late, the clamp's edge: a dwell of 900 s
    ("09:58:01", "10:07:30"),  # reached 7,201 s late, past it: no arrival
    ("10:11:30", "10:11:30"),
)
# Every stop before the last left without a dwell sample.
_UNPAIRED_LINE = (
    ("08:00:00", "08:00:00"),
    ("08:03:00", "08:03:30"),
    ("08:06:00", "08:06:20"),
    ("08:10:00", "08:10:00"),
)
_UNPAIRED_RUN = (
    ("08:00:20", "08:00:20"),
    ("08:03:40", "08:04:10"),  # standing through 08:04 only: no dwell
    ("08:06:30", "08:06:50"),  # standing through no poll: no arrival
    ("08:09:45", "08:09:45"),
)


def dwell(day: date) -> Scenario:
    """Three lines on one weekday whose feed reports arrivals as well as
    departures, each in its own stop update, and each run's last stop only
    as its arrival, which gives a running time and no dwell.

    The dwell line's two runs between them stand at a stop through two polls
    or more, through one, and through none, the first stop through three on
    one run and through none on the other, and plan dwells of 30 s to a
    five-minute layover, so an arrival taken at the planned departure, or a
    dwell taken as the difference of the two delays, would move their
    figures. A prediction before each run
    reaches a stop assumes the planned dwell, so where the dwell differs the
    departure reported after the arrival is a later reading than the
    prediction, and not its equal. The late line's one run, two hours late,
    reaches one stop at the clamp's edge and keeps its arrival, and stands
    there 15 minutes; it reaches the next a second past the edge, whose
    arrival is dropped. The unpaired line's one run gives running times and
    not one dwell sample."""
    line = _route("D1", "Dwell line", 6, 34.57)
    late = _route("D2", "Late line", 4, 34.59)
    unpaired = _route("D3", "Unpaired line", 4, 34.61)
    trips_and_runs = (
        (ScheduledTrip.from_clocks("D1_a", line.route_id, "平日", _DWELL_LINE_A), _DWELL_RUN_A),
        (ScheduledTrip.from_clocks("D1_b", line.route_id, "平日", _DWELL_LINE_B), _DWELL_RUN_B),
        (ScheduledTrip.from_clocks("D2_a", late.route_id, "平日", _LATE_LINE), _LATE_RUN),
        (ScheduledTrip.from_clocks("D3_a", unpaired.route_id, "平日", _UNPAIRED_LINE), _UNPAIRED_RUN),
    )
    return Scenario(
        name="dwell",
        routes=(line, late, unpaired),
        timetable=tuple(trip for trip, _run in trips_and_runs),
        days=(ServiceDay(day, "平日"),),
        runs=tuple(TripRun.from_clocks(trip, day, run) for trip, run in trips_and_runs),
        feed=FeedModel(poll_interval_sec=60, lead_sec=300, arrivals=True),
    )


# The source-mix scenario's day line: six stops two minutes apart, four
# trips ten minutes apart, delays in seconds per stop, the same on every
# day. Each run enters the feed two minutes before it leaves, and the
# collector pushes the app one poll in five, on the clock's fifth minutes.
# No push falls between the 07:10 and 07:30 runs entering the feed and
# leaving their first stops, so the live path never sees those departures.
_SOURCE_DAY_LINE = {
    "07:00:00": (20, 40, 60, 61, 90, 120),
    "07:10:00": (0, 60, 120, 180, 240, 300),
    "07:20:00": (30, 30, 30, 30, 30, 30),
    "07:30:00": (-90, -70, -50, -30, 0, 10),
}
# The 07:20 run is predicted 400 s late at its third stop onward until
# 07:23. Its third stop is last pushed at 07:20, inside that stretch, and
# last archived at 07:24, after it.
_STALE_FROM, _STALE_UNTIL, _STALE_DELAY_SEC = "07:15:00", "07:23:00", 400
# The night line's one trip runs past midnight on the 平日.
_SOURCE_NIGHT_LINE = ("23:52:00", (0, 30, 60, 90, 120, 150))


def source_mix(day: date) -> Scenario:
    """A day line run to the same truth on three days that reach the app by
    different paths, the collector archiving every poll it captures each
    minute and pushing the app every fifth.

    The 土曜 before *day* reaches the app by the archive alone, as dense as
    the collector captured it. *day*, a 平日, is offered both ways, its
    archive ingested before promotion reaches it, so promotion must leave
    it to the archive. The 土曜 after is offered both ways too, promoted
    before its archive is ingested, so it stays as sparse as the pushes:
    the departures no push caught are missing, a stale prediction stands,
    and a gap opens where a departure went unseen. A night line runs only
    on *day*, past midnight into the next calendar day, which the live path
    holds, so *day*'s service day holds rows of both paths."""
    before, after = day - timedelta(days=1), day + timedelta(days=1)
    line = _route("M1", "Day line", 6, 34.63)
    night = _route("M2", "Night line", 6, 34.65)
    trips, runs, misreports = [], [], []
    for service_date, day_type, code in ((before, "土曜", "sa"), (day, "平日", "wd"), (after, "土曜", "sa")):
        for first, delays in _SOURCE_DAY_LINE.items():
            trip = _trip(line, day_type, first, 120, f"M1_{code}{first[:5].replace(':', '')}")
            if trip not in trips:
                trips.append(trip)
            runs.append(TripRun.from_delays(trip, service_date, delays))
        stale = next(t for t in trips if t.trip_id == f"M1_{code}0720")
        misreports.append(
            Misreport(
                stale.trip_id,
                service_date,
                _STALE_DELAY_SEC,
                gtfs_seconds(_STALE_FROM),
                gtfs_seconds(_STALE_UNTIL),
                (3, 4, 5, 6),
            )
        )
    night_trip = _trip(night, "平日", _SOURCE_NIGHT_LINE[0], 120, "M2_wd2352")
    return Scenario(
        name="source_mix",
        routes=(line, night),
        timetable=(*trips, night_trip),
        days=(ServiceDay(before, "土曜"), ServiceDay(day, "平日"), ServiceDay(after, "土曜")),
        runs=(*runs, TripRun.from_delays(night_trip, day, _SOURCE_NIGHT_LINE[1])),
        feed=FeedModel(poll_interval_sec=60, lead_sec=120, push_interval_sec=300, misreports=tuple(misreports)),
        deliveries=(
            Delivery(before, live=False, archive=True),
            Delivery(day, archive=True, archive_first=True),
            Delivery(after, archive=True),
        ),
    )


# The imperfect feed's line: eight stops two minutes apart, a trip every ten
# minutes from 07:00, each run's true delays in seconds per stop. The feed
# predicts each stop at the delay of the last stop the run has left, half a
# minute late, so a poll captured on the minute lists what was so at the
# half minute before it.
_PROPAGATED_TRUTH = {
    "07:00:00": (0, 40, 80, 120, 180, 240, 301, 361),
    "07:10:00": (300, 250, 200, 150, 100, 61, 30, -30),
    "07:20:00": (-90, -60, -30, 0, 0, 0, 0, 0),
    "07:30:00": (0, 0, 0, 0, 0, 0, 0, 0),
    "07:40:00": (100, 30, 30, 30, 30, 30, 30, 30),
}
_FRESHNESS_LAG_SEC = 30


def imperfect_feed(day: date) -> Scenario:
    """A ten-minute line on one weekday whose feed propagates each run's
    current delay to every stop still to come, half a minute behind the run,
    under a header that does not show the lag.

    The runs lose time, recover it, leave early, keep time, and leave two
    stops 50 s apart, so the kept predictions fall on the other side of the
    on-time and five-minute edges from the truth at some stops and equal it
    at others, a stop is still listed after the run left it, and the lag
    changes which stop a prediction comes from.
    ``tests/unit/test_scenario_generator.py`` pins each stop's kept delay,
    true delay and lead."""
    route = _route("P1", "Propagated line", 8, 34.67)
    trips, runs = _runs(route, "平日", day, 120, _PROPAGATED_TRUTH)
    return Scenario(
        name="imperfect_feed",
        routes=(route,),
        timetable=tuple(trips),
        days=(ServiceDay(day, "平日"),),
        runs=tuple(runs),
        feed=FeedModel(
            prediction="propagate_current_delay",
            poll_interval_sec=60,
            lead_sec=300,
            freshness_lag_sec=_FRESHNESS_LAG_SEC,
        ),
    )


# The cutover line: six stops two minutes apart. The night's last trip
# leaves its first stop at 27:46 and is late enough from its fourth stop on
# to leave after the 04:00 cutover; the next morning's first trip leaves at
# 04:05. Each first departure, then each stop's true delay in seconds.
_CUTOVER_NIGHT = ("27:46:00", (300, 420, 540, 600, 660, 720))
_CUTOVER_MORNING = ("04:05:00", (30, 60, 61, 120, 180, 240))
# A trip of each day well clear of the cutover, so each day's timetable has
# a scheduled headway, far from high-frequency.
_CUTOVER_EVENING = ("22:00:00", (0, 30, 60, 90, 120, 150))
_CUTOVER_FORENOON = ("10:00:00", (-30, 0, 30, 60, 90, 120))
# Both trips enter the feed ten minutes before they leave, so each is
# predicted on both sides of the cutover, and until 04:00 the feed predicts
# the stops still to leave after it at this stale delay.
_CUTOVER_LEAD_SEC = 600
_CUTOVER_STALE_DELAY_SEC = 0
_CUTOVER_SEC = 4 * 3600


def cutover(day: date) -> Scenario:
    """A night's last trip that leaves its last three stops after the 04:00
    cutover, and the next service day's first trip, which the feed starts
    predicting before it. Every stop either trip leaves after the cutover is
    predicted before it, at a stale delay, and reported after it at its
    true one: each is one departure, of its own trip's service day. Each day
    runs one more trip, hours from the cutover."""
    route = _route("C1", "Cutover line", 6, 34.69)
    morning_day = day + timedelta(days=1)
    night = _trip(route, "平日", _CUTOVER_NIGHT[0], 120, "C1_wd2746")
    morning = _trip(route, "土曜", _CUTOVER_MORNING[0], 120, "C1_sa0405")
    evening = _trip(route, "平日", _CUTOVER_EVENING[0], 120, "C1_wd2200")
    forenoon = _trip(route, "土曜", _CUTOVER_FORENOON[0], 120, "C1_sa1000")
    night_enters = gtfs_seconds(_CUTOVER_NIGHT[0]) - _CUTOVER_LEAD_SEC
    morning_enters = gtfs_seconds(_CUTOVER_MORNING[0]) - _CUTOVER_LEAD_SEC
    return Scenario(
        name="cutover",
        routes=(route,),
        timetable=(evening, night, morning, forenoon),
        days=(ServiceDay(day, "平日"), ServiceDay(morning_day, "土曜")),
        runs=(
            TripRun.from_delays(evening, day, _CUTOVER_EVENING[1]),
            TripRun.from_delays(night, day, _CUTOVER_NIGHT[1]),
            TripRun.from_delays(morning, morning_day, _CUTOVER_MORNING[1]),
            TripRun.from_delays(forenoon, morning_day, _CUTOVER_FORENOON[1]),
        ),
        feed=FeedModel(
            poll_interval_sec=60,
            lead_sec=_CUTOVER_LEAD_SEC,
            misreports=(
                Misreport(
                    night.trip_id,
                    day,
                    _CUTOVER_STALE_DELAY_SEC,
                    night_enters,
                    _CUTOVER_SEC + 24 * 3600,
                    (4, 5, 6),
                ),
                Misreport(morning.trip_id, morning_day, _CUTOVER_STALE_DELAY_SEC, morning_enters, _CUTOVER_SEC),
            ),
        ),
    )


@dataclass(frozen=True, repr=False)
class Rounded:
    """An exact figure a column or report holds rounded to *places* decimals.
    A reading within half a unit of it matches, plus a little slack for a
    single-precision column."""

    exact: Fraction
    places: int

    def matches(self, actual) -> bool:
        if actual is None:
            return False
        slack = Fraction(1, 2 * 10**self.places) + Fraction(1, 10**4)
        return abs(Fraction(str(actual)) - self.exact) <= slack

    def __repr__(self) -> str:
        return f"{float(self.exact):.{self.places + 2}f} (to {self.places} dp)"


@dataclass(frozen=True, repr=False)
class Single:
    """An exact figure a single-precision (``REAL``) column holds. A reading
    within the column's rounding of it, half a unit in its 24-bit mantissa,
    matches; the tolerance is twice that, for a figure rounded once more on
    its way into the column."""

    exact: Fraction

    def matches(self, actual) -> bool:
        if actual is None:
            return False
        return abs(Fraction(actual) - self.exact) <= abs(self.exact) / 2**23

    def __repr__(self) -> str:
        return f"{float(self.exact)!r} (single precision)"


def _matches(expected, actual) -> bool:
    if isinstance(expected, (Rounded, Single)):
        return expected.matches(actual)
    return expected == actual


def _mean_min(f: oracle.Figures) -> Rounded:
    return Rounded(f.mean_delay_sec / 60, 2)


def _pct(share: Fraction) -> Rounded:
    return Rounded(share * 100, 1)


_TABLE_COLUMNS = {
    "agg_route_daily_dist": "route_code, date, service_type, samples, sum_delay_sec, on_time_count, late5_count",
    "agg_route_daily": (
        "route_code, date, service_type, samples, sum_delay_sec, avg_delay_sec, worst_delay_sec, trips_observed"
    ),
    "agg_daily_trend": "route_code, date, service_type, samples, sum_delay_sec, avg_min",
    "agg_route_stats": (
        "route_code, service_type, samples, sum_delay_sec, avg_min, on_time_pct, late5_pct, late_5min_plus"
    ),
    "agg_hour_daily": "date, hour, samples, sum_delay_sec",
    "agg_route_hour": "route_code, service_type, scheduled_time, samples, sum_delay_sec",
    "agg_route_hour_dow": "route_code, service_type, dow, hour, samples, sum_delay_sec",
    "agg_stop_daily": "stop_id, date, service_type, time_band, samples, delay_sum",
    "agg_schedule_revision_daily": "date, static_version_id",
    "agg_static_version_summary": "static_version_id, trip_count",
    "planned_trips_daily": "service_date, planned_trips",
    "agg_service_delivered_daily": "date, route_code, non_executed_trips",
    "agg_route_headway_daily": (
        "route_code, date, actual_headway_median_sec, actual_samples, actual_headway_sum_sec, "
        "actual_headway_sumsq_sec2, long_gap_count"
    ),
    "agg_feed_health": "date, raw_samples, clamp_count, total_rows, archive_rows, live_rows",
    "feed_quality_monthly": (
        "month, raw_samples, clamp_count, clamp_pct, clamp_missing, message_age_polls, "
        "message_age_undated_polls, message_age_p50_sec, message_age_p95_sec, message_age_missing"
    ),
    "agg_route_daily_dwell_run": (
        "route_code, date, service_type, dwell_samples, dwell_sum_sec, run_samples, run_sum_sec, "
        "(SELECT SUM(h) FROM unnest(hist_dwell) h) AS dwell_hist_total, "
        "(SELECT SUM(h) FROM unnest(hist_run) h) AS run_hist_total"
    ),
}
# Each read's report and query. The default, the industry window, puts
# readings exactly a minute early and exactly five minutes late on its edges;
# the legacy preset counts every early departure up to a minute late.
_REPORTS = {
    "on_time": ("on_time", ""),
    "route_table": ("route_table", "&include_sparse=1"),
    "route_table_legacy": ("route_table", "&include_sparse=1&preset=legacy_60s"),
}


@dataclass(frozen=True)
class Observed:
    """One scenario's run: the polls captured under each timetable version,
    and what the pipeline produced: each table's rows, a ``time`` or an
    ``interval`` as its ``HH:MM:SS`` text; each route report's rows; the trend
    report's first row; the council summary of every window ``_windows``
    names, with its definition stamp's source mix; the route table's
    response for each time band the truth departs
    in; the headway quality response for every window ``_windows`` names;
    the agency's row of the agencies board over the whole period; the feed
    quality the route table's definition stamp states over it; and the dwell
    report's payload for every window ``_windows`` names. ``truth`` is the
    oracle's outcome of the scenario, derived once, since deriving it
    simulates every poll."""

    scenario: Scenario
    truth: oracle.Outcome
    batches: list[tuple[TimetableVersion, list[Poll]]]
    tables: dict[str, list[dict]]
    reports: dict[str, list]
    trend: dict[str, Any]
    council: dict[tuple[date, date], dict[str, Any]]
    banded: dict[str, dict[str, Any]]
    headways: dict[tuple[date, date], dict[str, Any]]
    board: dict[str, Any]
    stamp: dict[str, Any]
    dwell: dict[tuple[date, date], dict[str, Any]]

    @cached_property
    def choice(self) -> Choice:
        """The outcome every comparison of this run is held to, chosen once."""
        return _choose(self)


def _window(scenario: Scenario) -> str:
    days = [d.date for d in scenario.days]
    return f"from={min(days)}&to={max(days)}"


def _windows(scenario: Scenario) -> list[tuple[date, date]]:
    """The whole period, then each service day alone."""
    days = sorted(d.date for d in scenario.days)
    return [(days[0], days[-1]), *((d, d) for d in days if len(days) > 1)]


def _council_label(part: str) -> str:
    """A label for one part of the council summary; its cells are keyed by (from, to)."""
    return f"GET reports/council_summary, {part}"


def _board_label(scenario: Scenario) -> str:
    """The label of the agency's row of the agencies board over the whole period."""
    return _clamp_label(f"GET network/summary {_window(scenario)}")


def _stamp_label(scenario: Scenario) -> str:
    """The label of the feed quality the route table's definition stamp states over the whole period."""
    return _clamp_label(f"definition stamp of GET reports/route_table {_window(scenario)}")


# A comparison is a set of cells: (table or report, row key) -> {column: value}.
Cells = dict[tuple[str, Hashable], dict[str, Any]]


def _cells(label: str, rows: dict) -> Cells:
    return {(label, key): columns for key, columns in rows.items()}


def _keyed(label: str, rows: list[dict], columns: tuple[str, ...]) -> Cells:
    return {(label, tuple(r[c] for c in columns)): r for r in rows}


def _sums(f: oracle.Figures) -> dict:
    return {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec}


def _daily_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    days = oracle.by_route_day(found)
    return (
        _cells(
            "agg_route_daily_dist",
            {k: {**_sums(f), "on_time_count": f.on_time, "late5_count": f.over_five_min} for k, f in days.items()},
        )
        | _cells(
            "agg_route_daily",
            {
                k: {
                    **_sums(f),
                    "avg_delay_sec": Rounded(f.mean_delay_sec, 0),
                    "worst_delay_sec": f.worst_delay_sec,
                    "trips_observed": f.trip_runs,
                }
                for k, f in days.items()
            },
        )
        | _cells("agg_daily_trend", {k: {**_sums(f), "avg_min": _mean_min(f)} for k, f in days.items()})
        | _cells(
            "agg_stop_daily, time bands summed",
            {k: {"samples": f.departures, "delay_sum": f.delay_sum_sec} for k, f in oracle.by_stop_day(found).items()},
        )
        | _cells(
            "agg_route_stats",
            {
                k: {
                    **_sums(f),
                    "avg_min": _mean_min(f),
                    "on_time_pct": _pct(f.on_time_share),
                    "late5_pct": _pct(f.over_five_min_share),
                    "late_5min_plus": f.over_five_min,
                }
                for k, f in oracle.by_route_day_type(found).items()
            },
        )
    )


def _daily_actual(observed: Observed) -> Cells:
    """The daily and pooled tables. The stop table's rows are summed over
    their time bands. The stop comes from the timetable each row was
    ingested against, kept in the archive once a revision replaces it."""
    tables = observed.tables
    key = ("route_code", "date", "service_type")
    stops: dict = {}
    for row in tables["agg_stop_daily"]:
        cell = stops.setdefault((row["stop_id"], row["date"], row["service_type"]), {"samples": 0, "delay_sum": 0})
        cell["samples"] += row["samples"]
        cell["delay_sum"] += row["delay_sum"]
    return (
        _keyed("agg_route_daily_dist", tables["agg_route_daily_dist"], key)
        | _keyed("agg_route_daily", tables["agg_route_daily"], key)
        | _keyed("agg_daily_trend", tables["agg_daily_trend"], key)
        | _cells("agg_stop_daily, time bands summed", stops)
        | _keyed("agg_route_stats", tables["agg_route_stats"], ("route_code", "service_type"))
    )


def _reports_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """The route reports over the whole period. Each lists a route only once
    it departs MIN_GROUP_SAMPLES times, include_sparse or not."""
    window = _window(observed.scenario)
    routes = {route: f for route, f in oracle.by_route(found).items() if f.departures >= MIN_GROUP_SAMPLES}
    return (
        _cells(
            f"GET reports/on_time {window}",
            {
                route: {"on_time_pct": _pct(f.on_time_industry_share), "avg_min": _mean_min(f), "samples": f.departures}
                for route, f in routes.items()
            },
        )
        | _cells(f"GET reports/route_table {window}", {route: _route_table_figures(f) for route, f in routes.items()})
        | _cells(
            f"GET reports/route_table {window}&preset=legacy_60s",
            {
                route: {"on_time_pct": _pct(f.on_time_share), "early_pct": _pct(f.early_share)}
                for route, f in routes.items()
            },
        )
    )


def _reports_actual(observed: Observed) -> Cells:
    window = _window(observed.scenario)
    reports = observed.reports
    # on_time rows: (route_code, service, on_time_pct, avg_min, samples, ...)
    # route_table rows: (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, early_pct, ...)
    return (
        _cells(
            f"GET reports/on_time {window}",
            {r[0]: {"on_time_pct": r[2], "avg_min": r[3], "samples": r[4]} for r in reports["on_time"]},
        )
        | _cells(f"GET reports/route_table {window}", _route_table_rows(reports["route_table"]))
        | _cells(
            f"GET reports/route_table {window}&preset=legacy_60s",
            {r[0]: {"on_time_pct": r[2], "early_pct": r[6]} for r in reports["route_table_legacy"]},
        )
    )


def _route_table_figures(f: oracle.Figures) -> dict:
    """The route table's figures for *f* under the default window, the industry one."""
    return {
        "avg_min": _mean_min(f),
        "on_time_pct": _pct(f.on_time_industry_share),
        "late5_pct": _pct(f.over_five_min_share),
        "late5_count": f.over_five_min,
        "samples": f.departures,
        "early_pct": _pct(f.early_share),
    }


def _route_table_rows(rows: list) -> dict:
    # route_table rows: (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, early_pct, ...)
    return {
        r[0]: {
            "avg_min": r[1],
            "on_time_pct": r[2],
            "late5_pct": r[3],
            "late5_count": r[4],
            "samples": r[5],
            "early_pct": r[6],
        }
        for r in rows
    }


def _banded_label(observed: Observed, band: str) -> str:
    return f"GET reports/route_table {_window(observed.scenario)}&time_band={band}"


def _banded_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """The route table of each time band the truth departs in, from a live
    scan of the observations, as the oracle files each departure: under the
    timetable in force on its service date. The table lists a route only
    once it departs MIN_GROUP_SAMPLES times, include_sparse or not."""
    cells: Cells = {}
    for band in observed.banded:
        routes = oracle.by_route(found, lambda d, band=band: d.time_band == band)
        listed = {route: _route_table_figures(f) for route, f in routes.items() if f.departures >= MIN_GROUP_SAMPLES}
        cells |= _cells(_banded_label(observed, band), listed)
        cells |= _cells(_banded_label(observed, band), {"definition": {"source": "live"}})
    return cells


def _banded_actual(observed: Observed) -> Cells:
    cells: Cells = {}
    for band, response in observed.banded.items():
        cells |= _cells(_banded_label(observed, band), _route_table_rows(response["rows"]))
        cells |= _cells(_banded_label(observed, band), {"definition": {"source": response["definition"]["source"]}})
    return cells


def _side_label(table: str, after: bool) -> str:
    return f"{table}, departures {'at 24:00 or later' if after else 'before 24:00'}"


def _hourly_expected(observed: Observed, found: oracle.Outcome, *, after: bool) -> Cells:
    """The departures on one side of 24:00 (*after* it, or before), bucket by
    bucket under the oracle's hour, weekday and scheduled time. A departure
    after midnight belongs to its service day's hour 24, 25, ..."""
    where = oracle.after_midnight if after else oracle.before_midnight
    return (
        _cells(_side_label("agg_hour_daily", after), {k: _sums(f) for k, f in oracle.by_day_hour(found, where).items()})
        | _cells(
            _side_label("agg_route_hour", after),
            {
                (route, day_type, gtfs_clock(sec)): _sums(f)
                for (route, day_type, sec), f in oracle.by_route_day_type_time(found, where).items()
            },
        )
        | _cells(
            _side_label("agg_route_hour_dow", after),
            {k: _sums(f) for k, f in oracle.by_route_day_type_weekday_hour(found, where).items()},
        )
    )


def _hourly_actual(observed: Observed, *, after: bool) -> Cells:
    tables = observed.tables

    def side(rows: list[dict], is_after) -> list[dict]:
        return [r for r in rows if is_after(r) == after]

    return (
        _keyed(
            _side_label("agg_hour_daily", after),
            side(tables["agg_hour_daily"], lambda r: r["hour"] >= 24),
            ("date", "hour"),
        )
        | _keyed(
            _side_label("agg_route_hour", after),
            side(tables["agg_route_hour"], lambda r: r["scheduled_time"] >= "24:00:00"),
            ("route_code", "service_type", "scheduled_time"),
        )
        | _keyed(
            _side_label("agg_route_hour_dow", after),
            side(tables["agg_route_hour_dow"], lambda r: r["hour"] >= 24),
            ("route_code", "service_type", "dow", "hour"),
        )
    )


def _trend_label(observed: Observed) -> str:
    return f"GET reports/trend {_window(observed.scenario)}"


def _timetable_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each day's timetable version and planned trips, each version's trip
    count, the trend report's revision boundaries, and the council summary's
    planned trips and the days they cover."""
    windows = {}
    for window in _windows(observed.scenario):
        days = [day for day in found.service if window[0] <= day <= window[1]]
        windows[window] = {
            "planned_trips": sum(found.service[day].planned for day in days),
            "covered_days": len(days),
            "window_days": (window[1] - window[0]).days + 1,
        }
    return (
        _cells(
            "agg_schedule_revision_daily",
            {(day,): {"static_version_id": name} for day, name in found.day_versions.items()},
        )
        | _cells(
            "agg_static_version_summary",
            {(name,): {"trip_count": trips} for name, trips in found.version_trips.items()},
        )
        | _cells("planned_trips_daily", {(day,): {"planned_trips": s.planned} for day, s in found.service.items()})
        | _cells(
            _trend_label(observed),
            {"revision_boundaries": {"days": [d.isoformat() for d in oracle.revision_boundaries(found)]}},
        )
        | _cells(_council_label("planned trips"), windows)
    )


def _timetable_actual(observed: Observed) -> Cells:
    tables = observed.tables
    return (
        _keyed("agg_schedule_revision_daily", tables["agg_schedule_revision_daily"], ("date",))
        | _keyed("agg_static_version_summary", tables["agg_static_version_summary"], ("static_version_id",))
        | _keyed("planned_trips_daily", tables["planned_trips_daily"], ("service_date",))
        | _cells(_trend_label(observed), {"revision_boundaries": {"days": observed.trend["revision_boundaries"]}})
        | _cells(_council_label("planned trips"), observed.council)
    )


def _not_run_by_route(scenario: Scenario) -> dict[tuple[date, str], int]:
    """Each day's trips reported not run, under the route each one's trip runs on."""
    counts: dict[tuple[date, str], int] = {}
    for c in scenario.cancellations:
        key = (c.service_date, scenario.trip(c.trip_id, c.service_date).route_id)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _service_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """The trips reported not run on each day, and over each council window
    the executed trips and service-delivered rate and the observation
    coverage, under the oracle's service-delivered semantics. Every
    scenario's days all hold observations, so coverage runs to each window's
    last day."""
    delivered, coverage = {}, {}
    for window in _windows(observed.scenario):
        in_window = [s for day, s in found.service.items() if window[0] <= day <= window[1]]
        planned = sum(s.planned for s in in_window)
        executed, seen = sum(s.executed for s in in_window), sum(s.observed for s in in_window)
        if oracle.delivered_rate_shown(found, window[1]):
            delivered[window] = {
                "executed_trips": executed,
                "service_delivered_pct": _pct(Fraction(executed, planned)),
                "delivered_missing": None,
            }
        else:
            delivered[window] = {
                "executed_trips": None,
                "service_delivered_pct": None,
                "delivered_missing": "feed_reports_no_cancellations",
            }
        coverage[window] = {
            "observed_trips": seen,
            "coverage_planned_trips": planned,
            "coverage_pct": _pct(Fraction(seen, planned)),
        }
    return (
        _cells(
            "agg_service_delivered_daily",
            {key: {"non_executed_trips": n} for key, n in _not_run_by_route(observed.scenario).items()},
        )
        | _cells(_council_label("service delivered"), delivered)
        | _cells(_council_label("coverage"), coverage)
    )


def _service_actual(observed: Observed) -> Cells:
    return (
        _keyed("agg_service_delivered_daily", observed.tables["agg_service_delivered_daily"], ("date", "route_code"))
        | _cells(_council_label("service delivered"), observed.council)
        | _cells(_council_label("coverage"), observed.council)
    )


def _headway_daily_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each route's actual headways on each service day: their median,
    count, sum and sum of squares, and how many are long."""
    return _cells(
        "agg_route_headway_daily",
        {
            key: {
                "actual_headway_median_sec": Single(day.median_sec),
                "actual_samples": day.samples,
                "actual_headway_sum_sec": Single(Fraction(day.sum_sec)),
                "actual_headway_sumsq_sec2": Single(Fraction(day.sumsq_sec2)),
                "long_gap_count": day.long_gaps,
            }
            for key, day in oracle.headway_days(found).items()
        },
    )


def _headway_daily_actual(observed: Observed) -> Cells:
    return _keyed("agg_route_headway_daily", observed.tables["agg_route_headway_daily"], ("route_code", "date"))


def _headway_label(window: tuple[date, date]) -> str:
    return f"GET headway_quality from={window[0]}&to={window[1]}"


# The headway quality columns compared, and the reason the panel gives for
# being empty when no route is high-frequency.
_HEADWAY_COLUMNS = ("ewt_sec", "cov", "long_gap_rate", "samples")
_NO_HIGH_FREQUENCY_ROUTES = "no_high_frequency_routes"


def _headway_quality_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Over every window ``_windows`` names, each route's headway quality,
    and whether the panel says no route is high-frequency. The excess wait
    comes from single-precision sums, so it is held to a hundredth of a
    second."""
    cells: Cells = {}
    for window in _windows(observed.scenario):
        label = _headway_label(window)
        cells |= _cells(
            label,
            {
                route: {
                    "ewt_sec": Rounded(quality.excess_wait_sec, 2),
                    "cov": None if quality.cov is None else Rounded(Fraction(quality.cov), 4),
                    "long_gap_rate": Rounded(quality.long_gap_share, 4),
                    "samples": quality.samples,
                }
                for route, quality in oracle.headway_quality(found, *window).items()
            },
        )
        reason = _NO_HIGH_FREQUENCY_ROUTES if oracle.no_high_frequency_route(found, *window) else None
        cells |= _cells(label, {"panel": {"unavailable_reason": reason}})
    return cells


def _headway_quality_actual(observed: Observed) -> Cells:
    cells: Cells = {}
    for window, response in observed.headways.items():
        label = _headway_label(window)
        cells |= _cells(label, {r["route_code"]: {c: r[c] for c in _HEADWAY_COLUMNS} for r in response["rows"]})
        cells |= _cells(label, {"panel": {"unavailable_reason": response["unavailable_reason"]}})
    return cells


def _share(share: Fraction | None) -> Rounded | None:
    """A clamp share as the board, the stamp and the scorecard hold it, to two decimals."""
    return None if share is None else Rounded(share, 2)


def _clamp_label(surface: str) -> str:
    return f"implausible readings, {surface}"


def _month_label(part: str) -> str:
    return f"feed_quality_monthly, {part}"


def _clamp_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each service day's readings, implausible readings and rows, and the
    clamp share the board, the stamp, the council summary and each month of
    the scorecard state over their own days."""
    period = oracle.readings_over(found, *_windows(observed.scenario)[0])
    share = period.clamp_share
    months = {
        (month,): {
            "raw_samples": card.readings.readings,
            "clamp_count": card.readings.implausible,
            "clamp_pct": _share(card.readings.clamp_share),
            "clamp_missing": None if card.readings.clamp_share is not None else "no_data",
        }
        for month, card in oracle.scorecard(found).items()
    }
    return (
        _cells(
            "agg_feed_health",
            {
                (day,): {
                    "raw_samples": r.readings,
                    "clamp_count": r.implausible,
                    "total_rows": r.rows,
                }
                for day, r in found.readings.items()
            },
        )
        | _cells(
            _board_label(observed.scenario),
            {"agency": {"raw_samples": period.readings, "clamp_count": period.implausible, "clamp_pct": _share(share)}},
        )
        | _cells(
            _stamp_label(observed.scenario),
            {
                "agency": {
                    "clamp_pct": _share(share),
                    "clamp_missing": None if share is not None else "no_data",
                    "clamp_high": oracle.clamp_high(share),
                }
            },
        )
        | _cells(
            _clamp_label(_council_label("comparability")),
            {
                w: {"clamp_pct": _share(oracle.readings_over(found, *w).clamp_share)}
                for w in _windows(observed.scenario)
            },
        )
        | _cells(_month_label("implausible readings"), months)
    )


def _clamp_actual(observed: Observed) -> Cells:
    return (
        _keyed("agg_feed_health", observed.tables["agg_feed_health"], ("date",))
        | _cells(_board_label(observed.scenario), {"agency": observed.board})
        | _cells(_stamp_label(observed.scenario), {"agency": observed.stamp})
        | _cells(_clamp_label(_council_label("comparability")), observed.council)
        | _keyed(_month_label("implausible readings"), observed.tables["feed_quality_monthly"], ("month",))
    )


def _sources_label(surface: str) -> str:
    return f"observations by path, {surface}"


def _sources_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each service day's rows by the path they reached history by, and over
    every window ``_windows`` names the days by source the definition stamp
    states, so each single day's window names the source that day ended
    with."""
    return _cells(
        _sources_label("agg_feed_health"),
        {(day,): {"archive_rows": s.archive_rows, "live_rows": s.live_rows} for day, s in found.sources.items()},
    ) | _cells(
        _sources_label("definition stamp of GET reports/council_summary"),
        {
            window: {
                "source_archive_days": mix.archive_days,
                "source_live_days": mix.live_days,
                "source_none_days": mix.none_days,
            }
            for window in _windows(observed.scenario)
            for mix in (oracle.source_mix(found, *window),)
        },
    )


def _sources_actual(observed: Observed) -> Cells:
    return _keyed(_sources_label("agg_feed_health"), observed.tables["agg_feed_health"], ("date",)) | _cells(
        _sources_label("definition stamp of GET reports/council_summary"), observed.council
    )


def _message_age_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each month's polls and the quantiles of their message ages, each
    quantile to a tenth of a second. Every poll's capture instant is known,
    whichever path delivered it: live by its capture time, archived by its
    collector's file name."""
    months = {}
    for month, card in oracle.scorecard(found).items():
        # No scenario leaves a month without a poll between two with one; such a month has no ages.
        assert card.ages is not None, f"{observed.scenario.name}: no poll in {month:%Y-%m}"
        months[(month,)] = {
            "message_age_polls": card.polls,
            "message_age_undated_polls": 0,
            "message_age_p50_sec": Rounded(card.ages[0], 1),
            "message_age_p95_sec": Rounded(card.ages[1], 1),
            "message_age_missing": None,
        }
    return _cells(_month_label("message age"), months)


def _message_age_actual(observed: Observed) -> Cells:
    return _keyed(_month_label("message age"), observed.tables["feed_quality_monthly"], ("month",))


def _dwell_daily_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Each route's dwell and running samples on each service day it has a
    visit on, none when no visit pairs, and the total of each histogram,
    which holds every sample once."""
    return _cells(
        "agg_route_daily_dwell_run",
        {
            key: {
                "dwell_samples": len(f.dwell),
                "dwell_sum_sec": sum(f.dwell),
                "dwell_hist_total": len(f.dwell),
                "run_samples": len(f.running),
                "run_sum_sec": sum(f.running),
                "run_hist_total": len(f.running),
            }
            for key, f in oracle.dwell_run_by_route_day(found).items()
        },
    )


def _dwell_daily_actual(observed: Observed) -> Cells:
    return _keyed(
        "agg_route_daily_dwell_run",
        observed.tables["agg_route_daily_dwell_run"],
        ("route_code", "date", "service_type"),
    )


def _dwell_label(window: tuple[date, date]) -> str:
    return f"GET reports/dwell_run from={window[0]}&to={window[1]}"


# The dwell report's columns compared, and the reason it gives when the
# routes it was asked about hold no sample. Its percentiles read histogram
# buckets no document sets out, so they are not compared.
_DWELL_COLUMNS = ("dwell_samples", "dwell_avg_sec", "run_samples", "run_avg_sec")
_NO_PAIRED_SAMPLES = "no_paired_samples"


def _mean_sec(mean: Fraction | None) -> Rounded | None:
    """A mean the dwell report states unrounded, held to a ten-thousandth of a second."""
    return None if mean is None else Rounded(mean, 4)


def _dwell_report_expected(observed: Observed, found: oracle.Outcome) -> Cells:
    """Over every window ``_windows`` names, each route with a dwell or a
    running sample, its sample counts and means, and the report's state:
    available, since the runner confirms the arrival field, and saying when
    the window's routes hold no sample at all."""
    cells: Cells = {}
    for window in _windows(observed.scenario):
        label = _dwell_label(window)
        routes = oracle.dwell_run_by_route(found, *window)
        listed = {route: f for route, f in routes.items() if f.dwell or f.running}
        cells |= _cells(
            label,
            {
                route: {
                    "dwell_samples": len(f.dwell),
                    "dwell_avg_sec": _mean_sec(f.dwell_mean_sec),
                    "run_samples": len(f.running),
                    "run_avg_sec": _mean_sec(f.running_mean_sec),
                }
                for route, f in listed.items()
            },
        )
        reason = _NO_PAIRED_SAMPLES if routes and not listed else None
        cells |= _cells(label, {"report": {"available": True, "unavailable_reason": reason}})
    return cells


def _dwell_report_actual(observed: Observed) -> Cells:
    cells: Cells = {}
    for window, payload in observed.dwell.items():
        label = _dwell_label(window)
        cells |= _cells(label, {r["route_code"]: {c: r[c] for c in _DWELL_COLUMNS} for r in payload["routes"]})
        cells |= _cells(
            label,
            {"report": {"available": payload["available"], "unavailable_reason": payload["unavailable_reason"]}},
        )
    return cells


Expect = Callable[[Observed, oracle.Outcome], Cells]
# Each test's comparison: the cells the oracle expects of an outcome, and the
# cells the run produced.
_DAILY = (_daily_expected, _daily_actual)
_REPORTS_COMPARED = (_reports_expected, _reports_actual)
_BEFORE_MIDNIGHT = (partial(_hourly_expected, after=False), partial(_hourly_actual, after=False))
_AFTER_MIDNIGHT = (partial(_hourly_expected, after=True), partial(_hourly_actual, after=True))
_TIMETABLE = (_timetable_expected, _timetable_actual)
_SERVICE = (_service_expected, _service_actual)
_BANDED = (_banded_expected, _banded_actual)
_HEADWAY_DAILY = (_headway_daily_expected, _headway_daily_actual)
_HEADWAY_QUALITY = (_headway_quality_expected, _headway_quality_actual)
_CLAMP_SHARE = (_clamp_expected, _clamp_actual)
_MESSAGE_AGE = (_message_age_expected, _message_age_actual)
_SOURCES = (_sources_expected, _sources_actual)
_DWELL_DAILY = (_dwell_daily_expected, _dwell_daily_actual)
_DWELL_REPORT = (_dwell_report_expected, _dwell_report_actual)
_COMPARISONS = (
    _DAILY,
    _REPORTS_COMPARED,
    _BEFORE_MIDNIGHT,
    _AFTER_MIDNIGHT,
    _TIMETABLE,
    _SERVICE,
    _BANDED,
    _HEADWAY_DAILY,
    _HEADWAY_QUALITY,
    _CLAMP_SHARE,
    _MESSAGE_AGE,
    _SOURCES,
    _DWELL_DAILY,
    _DWELL_REPORT,
)


@dataclass(frozen=True)
class Divergence:
    """A known way the pipeline departs from the truth. ``view`` turns an
    outcome into the outcome the pipeline computes instead, from the run's
    scenario and polls; ``reason`` states the divergence, and ``name`` names
    it in a mismatch report."""

    name: str
    reason: str
    view: Callable[[Observed, oracle.Outcome], oracle.Outcome]


def _lower_middle(plan: oracle.Plan) -> oracle.Plan:
    ordered = sorted(plan.gaps)
    return replace(plan, headway_sec=Fraction(ordered[(len(ordered) - 1) // 2]) if ordered else None)


def _scheduled_headway_is_the_lower_middle_gap(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    return replace(
        found,
        plans={key: _lower_middle(plan) for key, plan in found.plans.items()},
        wait_plans={key: _lower_middle(plan) for key, plan in found.wait_plans.items()},
    )


def _the_loaded_timetable_stands_for_every_day(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """Each day's high-frequency test and scheduled mean wait read the plan
    the timetable loaded last gives the route on its newest day, and a route
    that timetable has no plan for is high-frequency on no day. Long gaps keep
    each day's own plan. The pipeline stands the route's one dominant service
    in for every day, which is that newest day's plan only while the scenario
    runs one day type, so the view refuses any other."""
    scenario = observed.scenario
    if len({d.day_type for d in scenario.days}) != 1:
        raise ValueError(f"{scenario.name}: the loaded-timetable view models one day type only")
    loaded = scenario.versions[-1]
    newest = {
        d.day_type: d.date for d in sorted(scenario.days, key=lambda d: d.date) if scenario.version_on(d.date) == loaded
    }
    wait_plans = {}
    for route_id, day in found.wait_plans:
        stand_in = newest.get(scenario.day_type(day))
        plan = None if stand_in is None else found.plans.get((route_id, stand_in))
        if plan is not None:
            wait_plans[(route_id, day)] = plan
    return replace(found, wait_plans=wait_plans)


def _each_capture_day_files_its_own_departure(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """Each departure once for every service day its plausible readings were
    captured on, at the last of them captured that day, under its trip's day
    type: what filing a reading under its capture's service day, rather than
    its trip's, gives. Every other part of the outcome stays the truth's."""
    captured = [poll for _version, batch in observed.batches for poll in batch]
    sent = sorted(
        (r.poll for r in oracle.received(observed.scenario, captured)), key=lambda p: (p.captured_at, p.source_file)
    )
    last: dict[tuple[str, date, int, date], int] = {}
    for poll in sent:
        day = oracle.service_day_of(poll.captured_at)
        for entry in poll.trips:
            for stop in entry.stops:
                delay = stop.departure_delay
                if stop.status == SCHEDULED and delay is not None and oracle.plausible(delay):
                    last[(entry.trip_id, entry.service_date, stop.stop_sequence, day)] = delay
    departed = {(d.trip_id, d.service_date, d.stop_sequence): d for d in found.departures}
    refiled = [
        replace(departed[key[:3]], service_date=key[3], delay_sec=delay)
        for key, delay in last.items()
        if key[:3] in departed
    ]
    return replace(found, departures=tuple(refiled))


# The divergences below are met by no scenario (_KNOWN): each one's test holds
# every cell its rule would move to the truth, so the rule cannot come back
# unnoticed.
_LOWER_MIDDLE_DIVERGENCE = Divergence(
    name="scheduled headway is the lower middle gap",
    reason=(
        "Taking the lower of a route's two middle scheduled gaps (PERCENTILE_DISC(0.5)) rather than their mean "
        "reads a timetable whose gaps split evenly between long and short as its short gap, and classes the "
        "route high-frequency"
    ),
    view=_scheduled_headway_is_the_lower_middle_gap,
)
_LOADED_TIMETABLE_DIVERGENCE = Divergence(
    name="headway quality reads the loaded timetable",
    reason=(
        "Classing a route high-frequency, and measuring each day's excess wait, against the timetable loaded "
        "now (agg_route_headway) rather than the one in force on that day judges a day run under an earlier "
        "timetable by today's"
    ),
    view=_the_loaded_timetable_stands_for_every_day,
)
_CAPTURE_DAY_DIVERGENCE = Divergence(
    name="a reading is filed under its capture's service day",
    reason=(
        "Filing each reading under the service day it was captured on, rather than its trip's, counts a "
        "departure whose readings straddle the 04:00 cutover once on each day, the earlier at the last "
        "prediction before the cutover"
    ),
    view=_each_capture_day_files_its_own_departure,
)
# The divergences each scenario is known to meet, by scenario name.
_KNOWN: dict[str, tuple[Divergence, ...]] = {}


def _outcomes(observed: Observed) -> list[tuple[tuple[Divergence, ...], oracle.Outcome]]:
    """The truth's outcome, then its view under each combination of the
    scenario's known divergences, each with the divergences it applies."""
    divergences = _KNOWN.get(observed.scenario.name, ())
    truth = observed.truth
    out = []
    for n in range(len(divergences) + 1):
        for chosen in itertools.combinations(divergences, n):
            found = truth
            for divergence in chosen:
                found = divergence.view(observed, found)
            out.append((chosen, found))
    return out


def _fits(want: dict | None, got: dict | None) -> bool:
    if want is None or got is None:
        return want is got
    return all(_matches(value, got.get(column)) for column, value in want.items())


def _shown(cell: dict | None, columns) -> str:
    if cell is None:
        return "no row"
    return "{" + ", ".join(f"{c}: {cell.get(c)!r}" for c in columns) + "}"


def _unfit(actual: Cells, expected: Cells) -> list[tuple[str, Hashable]]:
    """The cells where *actual* does not fit *expected*, in order. A cell
    *expected* lacks expects no row."""
    return [
        cell for cell in sorted(set(actual) | set(expected), key=str) if not _fits(expected.get(cell), actual.get(cell))
    ]


def _compare(actual: Cells, expected: Cells, keep: Callable[[tuple[str, Hashable]], bool]) -> list[str]:
    """Each kept cell where *actual* does not fit *expected*, as one line."""
    lines = []
    for cell in _unfit(actual, expected):
        if keep(cell):
            want, got = expected.get(cell), actual.get(cell)
            columns = want or got or {}
            lines.append(f"{cell[0]} {cell[1]}: expected {_shown(want, columns)}, actual {_shown(got, columns)}")
    return lines


@dataclass(frozen=True)
class Choice:
    """The truth under one combination of a scenario's known divergences, and
    how many cells of the run, over every comparison, it does not fit."""

    divergences: tuple[Divergence, ...]
    outcome: oracle.Outcome
    unfit_cells: int

    def describe(self) -> str:
        under = ", ".join(d.name for d in self.divergences) or "no known divergence"
        fit = "every cell" if not self.unfit_cells else f"all but {self.unfit_cells} cells"
        return f"held to the truth under {under}, the combination that fits {fit} of the run"


def _choose(observed: Observed) -> Choice:
    """The one combination of known divergences the whole run is held to: the
    first that fits every cell of every comparison, or else the one that fits
    the most, the truth and then the smallest winning a tie. Chosen over the
    union of the comparisons, so tables and reports that disagree about the
    same trip cannot each pass under a combination of their own."""
    best: Choice | None = None
    for chosen, found in _outcomes(observed):
        unfit = sum(
            len(_compare(read(observed), expect(observed, found), lambda _cell: True)) for expect, read in _COMPARISONS
        )
        if best is None or unfit < best.unfit_cells:
            best = Choice(chosen, found, unfit)
        if not unfit:
            break
    assert best is not None
    return best


def _hard_mismatches(observed: Observed, comparison: tuple[Expect, Callable[[Observed], Cells]]) -> list[str]:
    """Every cell of *comparison* against the scenario's one chosen outcome
    (``Observed.choice``): the truth, or its view under the combination of
    known divergences that fits the whole run. A cell a divergence moves
    therefore takes exactly the figure every other comparison shows it to
    give, and anything else a cell holds is a failure."""
    expect, read = comparison
    choice = observed.choice
    lines = _compare(read(observed), expect(observed, choice.outcome), lambda _cell: True)
    return [f"({choice.describe()})", *lines] if lines else []


def _moved(truth: Cells, view: Cells) -> set[tuple[str, Hashable]]:
    """The cells *view* gives another figure than *truth* does, or a row it lacks."""
    return {cell for cell in set(truth) | set(view) if truth.get(cell) != view.get(cell)}


def _divergence_mismatches(observed: Observed, divergence: Divergence) -> list[str]:
    """The cells *divergence* moves and no other known divergence of the
    scenario does, against the truth alone."""
    others = [d for d in _KNOWN.get(observed.scenario.name, ()) if d is not divergence]
    truth = observed.truth
    lines: list[str] = []
    own_cells = 0
    for expect, read in _COMPARISONS:
        expected = expect(observed, truth)
        # Assumes each divergence moves cells on its own; one that shows only in
        # combination needs this extended, never the RuntimeError below weakened.
        moved = _moved(expected, expect(observed, divergence.view(observed, truth)))
        for other in others:
            moved -= _moved(expected, expect(observed, other.view(observed, truth)))
        own_cells += len(moved)
        lines += _compare(read(observed), expected, moved.__contains__)
    if not own_cells:
        raise RuntimeError("the divergence moves no cell of its own, so its test would test nothing")
    return lines


async def _run_scenario(
    scenario: Scenario, pg_conn, ch_client, tmp_dir: Path, now: datetime
) -> tuple[int, list[tuple[TimetableVersion, list[Poll]]]]:
    """Load, ingest, promote and analyze *scenario* for a new agency; return
    its id and the polls captured under each timetable version.

    Each timetable version is loaded as ``refresh_static`` loads a fetched
    zip, and its polls are handed over step by step (``handoff``) before the
    next version is loaded: the polls the collector pushed are ingested
    live, the archive taken before promotion is ingested and analyzed, as
    the archive job runs, the closed days are promoted and analyzed, as the
    promotion run does, and then the archive taken after promotion is
    ingested and analyzed."""
    from api.main import app
    from pipeline import day_types, live_filing, route_catalog, service_hours
    from pipeline.analyze import analyze
    from pipeline.ingest import ingest
    from pipeline.promote import promote_closed_days
    from pipeline.static_loader import load_static

    def ingest_archive(archived: tuple[Poll, ...], folder: Path) -> None:
        """Ingest *archived* as the collector's archive lays it out, then analyze."""
        for poll in archived:
            path = folder / poll.source_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(poll.payload)
        ingest(str(folder), agency_id, pg_conn, ch_client, strict=True)
        analyze(agency_id, pg_conn, ch_client)
        pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (f"scenario {scenario.name}", f"https://{scenario.name}.example.com/trip_updates.pb"),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()

    batches = poll_batches(scenario)
    steps = [(version, handoff(scenario, batch)) for version, batch in batches]
    check_closed([poll.captured_at for _version, batch in batches for poll in batch], now)
    check_closed_and_live([poll.captured_at for _version, step in steps for poll in step.pushed], now)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for version, step in steps:
            zip_path = tmp_dir / f"{version.name}.zip"
            zip_path.write_bytes(static_gtfs_zip(scenario, version))
            load_static(str(zip_path), agency_id, pg_conn, ch_client=ch_client)
            day_types.invalidate_day_types()
            route_catalog.invalidate_retired_routes()
            service_hours.invalidate_service_hours()
            live_filing.invalidate_schedule_joins()
            for poll in step.pushed:
                resp = await client.post(
                    f"/internal/collector/updates/{agency_id}",
                    content=poll.payload,
                    headers={
                        "X-Collector-Secret": _COLLECTOR_SECRET,
                        "X-Source-File": poll.source_file,
                        "X-Captured-At": poll.captured_at.isoformat(),
                        "Content-Type": "application/octet-stream",
                    },
                )
                if resp.status_code != 200 or resp.json()["inserted"] != poll.stored_rows:
                    raise RuntimeError(f"{poll.source_file} ({poll.stored_rows} rows): {resp.status_code} {resp.text}")
            if step.archived_first:
                ingest_archive(step.archived_first, tmp_dir / f"{version.name}_archived_first")
            promote_closed_days(agency_id, pg_conn, ch_client)
            analyze(agency_id, pg_conn, ch_client)
            pg_conn.commit()
            if step.archived_after:
                ingest_archive(step.archived_after, tmp_dir / f"{version.name}_archived_after")
    return agency_id, batches


def _clock_text(value):
    """A `time` or an `interval` as its ``HH:MM:SS``, hours past 23 kept; any other value as it is."""
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return gtfs_clock(int(value.total_seconds()))
    return value


def _read_tables(pg_conn, agency_id: int) -> dict[str, list[dict]]:
    out = {}
    with pg_conn.cursor() as cur:
        for table, columns in _TABLE_COLUMNS.items():
            cur.execute(f"SELECT {columns} FROM {table} WHERE agency_id = %s", (agency_id,))
            names = [c.name for c in cur.description]
            out[table] = [{n: _clock_text(v) for n, v in zip(names, row, strict=True)} for row in cur.fetchall()]
    return out


def _council_figures(response: dict) -> dict[str, Any]:
    # rows[0]: (on_time_pct, avg_delay_min, samples, planned_trips, executed_trips,
    #           service_delivered_pct, early_departure_pct)
    row = response["rows"][0]
    comparability = response["comparability"] or {}
    (mix,) = response["definition"]["source_mix"] or ({},)
    return {
        "planned_trips": row[3],
        "executed_trips": row[4],
        "service_delivered_pct": row[5],
        "covered_days": response["covered_days"],
        "window_days": response["window_days"],
        "delivered_missing": response["delivered_missing"],
        "observed_trips": comparability.get("observed_trips"),
        "coverage_planned_trips": comparability.get("coverage_planned_trips"),
        "coverage_pct": comparability.get("coverage_pct"),
        "clamp_pct": comparability.get("clamp_pct"),
        **{f"source_{k}": mix.get(k) for k in ("archive_days", "live_days", "none_days")},
    }


@dataclass(frozen=True)
class _Reads:
    """What the API returned for a run; ``Observed`` names each part."""

    reports: dict[str, list]
    trend: dict[str, Any]
    council: dict[tuple[date, date], dict[str, Any]]
    banded: dict[str, dict[str, Any]]
    headways: dict[tuple[date, date], dict[str, Any]]
    board: dict[str, Any]
    stamp: dict[str, Any]
    dwell: dict[tuple[date, date], dict[str, Any]]


async def _read_reports(scenario: Scenario, truth: oracle.Outcome, agency_id: int) -> _Reads:
    from api.main import app

    async def get(client, report: str, query: str, scoped: bool = True) -> dict:
        path = f"/api/{agency_id}/{report}" if scoped else f"/api/{report}"
        resp = await client.get(f"{path}?{query}")
        if resp.status_code != 200:
            raise RuntimeError(f"{report}?{query}: {resp.status_code} {resp.text}")
        return resp.json()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = {
            name: await get(client, f"reports/{report}", f"{_window(scenario)}{query}")
            for name, (report, query) in _REPORTS.items()
        }
        reports = {name: response["rows"] for name, response in responses.items()}
        (stamp,) = responses["route_table"]["definition"]["feed_quality"]
        (board,) = (await get(client, "network/summary", _window(scenario), scoped=False))["agencies"]
        trend = (await get(client, "reports/trend", _window(scenario)))["rows"][0]
        council = {
            window: _council_figures(await get(client, "reports/council_summary", f"from={window[0]}&to={window[1]}"))
            for window in _windows(scenario)
        }
        bands = sorted({d.time_band for d in truth.departures} - {None})
        banded = {
            band: await get(client, "reports/route_table", f"{_window(scenario)}&include_sparse=1&time_band={band}")
            for band in bands
        }
        headways = {
            window: await get(client, "headway_quality", f"from={window[0]}&to={window[1]}")
            for window in _windows(scenario)
        }
        dwell = {
            window: (await get(client, "reports/dwell_run", f"from={window[0]}&to={window[1]}"))["rows"][0]
            for window in _windows(scenario)
        }
    return _Reads(reports, trend, council, banded, headways, board, stamp, dwell)


def _empty_stores(pg_conn, ch_client, reset_sql: str) -> None:
    """Both stores as the conftest's per-test fixtures leave them: Postgres
    reset, ClickHouse observations, aggregates and timetable archive
    truncated, its work tables dropped."""
    from tests.conftest import _CH_STORE_TABLES, _drop_ch_work_tables

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(reset_sql)
    pg_conn.commit()
    for table in ("updates", "updates_live", *_CH_STORE_TABLES):
        ch_client.command(f"TRUNCATE TABLE IF EXISTS {table}")
    _drop_ch_work_tables(ch_client)


_ABSENT = object()


def _restore_app_state(app, saved: dict) -> None:
    for name, value in saved.items():
        if value is _ABSENT:
            if hasattr(app.state, name):
                delattr(app.state, name)
        else:
            setattr(app.state, name, value)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def observed(request, reset_sql, _ch_schema, tmp_path_factory) -> Observed:
    """One pipeline run per scenario, shared by every test of this module
    that names the scenario.

    The conftest's per-test fixtures reset both stores, so nothing the run
    wrote may outlive this fixture: it works on connections of its own,
    starts from emptied stores, reads everything the tests compare, empties
    the stores again, and hands the tests plain data. Every connection it
    opens is closed however the run ends.

    A strict xfail on a comparison expects an AssertionError, and pytest
    applies it to an error raised while this fixture sets up as well. So an
    AssertionError from anywhere in the run, the pipeline's own asserts
    included, is re-raised as RuntimeError: a broken run is an error, never
    an expected failure.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        pytest.skip("requires `make ch-test` (RUN_CH_INTEGRATION=1)")
    from api.main import app
    from api.middleware.ratelimit import limiter
    from api.sso import SSO_ENV
    from pipeline.cache import clear_all
    from tests.conftest import _CH_TEST, _ch_test_client, _database_url, _test_pool, confirm_rt_field_coverage

    now = datetime.now(timezone.utc)
    scenario = request.param(anchor_day(now))
    truth = oracle.outcome(scenario)
    try:
        async with contextlib.AsyncExitStack() as stack:
            pg_conn = psycopg2.connect(_database_url())
            stack.callback(pg_conn.close)
            ch_client = _ch_test_client()
            stack.callback(ch_client.close)
            ch_async = await clickhouse_connect.get_async_client(**{**_CH_TEST, "port": int(_CH_TEST["port"])})
            stack.push_async_callback(ch_async.close)
            stack.callback(_restore_app_state, app, {n: getattr(app.state, n, _ABSENT) for n in ("pool", "ch_client")})
            stack.callback(_empty_stores, pg_conn, ch_client, reset_sql)
            mp = stack.enter_context(pytest.MonkeyPatch.context())
            for var in SSO_ENV:
                mp.delenv(var, raising=False)
            mp.setenv("COLLECTOR_INGEST_SECRET", _COLLECTOR_SECRET)

            _empty_stores(pg_conn, ch_client, reset_sql)
            with pg_conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'Asia/Tokyo'")
            pg_conn.commit()
            clear_all()
            limiter.reset()
            agency_id, batches = await _run_scenario(
                scenario, pg_conn, ch_client, tmp_path_factory.mktemp(scenario.name), now
            )
            tables = _read_tables(pg_conn, agency_id)
            pool = await _test_pool()
            stack.push_async_callback(pool.close)
            await confirm_rt_field_coverage(pool, agency_id)
            app.state.pool, app.state.ch_client = pool, ch_async
            reads = await _read_reports(scenario, truth, agency_id)
    except AssertionError as exc:
        raise RuntimeError(f"scenario {scenario.name}: an assertion failed while the pipeline ran") from exc
    return Observed(
        scenario=scenario,
        truth=truth,
        batches=batches,
        tables=tables,
        reports=reads.reports,
        trend=reads.trend,
        council=reads.council,
        banded=reads.banded,
        headways=reads.headways,
        board=reads.board,
        stamp=reads.stamp,
        dwell=reads.dwell,
    )


def _assert_clean(mismatches: list[str]) -> None:
    assert not mismatches, "pipeline differs from the truth:\n" + "\n".join(mismatches)


# Every test parametrizes `observed` with these same param objects. pytest
# keeps a module-scoped fixture's value per parameter value and runs the tests
# that share one together, so each scenario's pipeline runs once for the
# module.
#
# Each comparison test holds every cell of every scenario to the truth, or to
# the view of it under one combination of the scenario's known divergences,
# chosen once for the whole run, so any other change to a cell still fails.
# Each divergence's strict xfail holds the cells it alone moves to the truth;
# fixing it makes that test pass, which strict mode fails until the marker and
# the divergence's _KNOWN entry go with the fix.
_ALL = [
    pytest.param(plain_weekday, id="plain_weekday"),
    pytest.param(night_route, id="night_route"),
    _TIMETABLE_CHANGE := pytest.param(timetable_change, id="timetable_change"),
    _PUBLISHED_EARLY := pytest.param(timetable_published_early, id="timetable_published_early"),
    pytest.param(cancellations, id="cancellations"),
    pytest.param(bunching, id="bunching"),
    pytest.param(clamp, id="clamp"),
    pytest.param(frozen_feed, id="frozen_feed"),
    pytest.param(dwell, id="dwell"),
    pytest.param(source_mix, id="source_mix"),
    _IMPERFECT := pytest.param(imperfect_feed, id="imperfect_feed"),
    _CUTOVER := pytest.param(cutover, id="cutover"),
]


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_daily_and_pooled_aggregates_match_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _DAILY))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_reports_match_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _REPORTS_COMPARED))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_hourly_tables_file_each_departure_before_midnight_under_its_scheduled_hour(observed):
    _assert_clean(_hard_mismatches(observed, _BEFORE_MIDNIGHT))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_hourly_tables_hold_the_departures_after_midnight(observed):
    _assert_clean(_hard_mismatches(observed, _AFTER_MIDNIGHT))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_each_day_is_planned_and_filed_under_the_timetable_in_force(observed):
    _assert_clean(_hard_mismatches(observed, _TIMETABLE))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_service_delivered_and_coverage_match_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _SERVICE))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_time_band_live_scans_match_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _BANDED))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_daily_headways_match_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _HEADWAY_DAILY))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_headway_quality_matches_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _HEADWAY_QUALITY))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_the_clamp_share_counts_every_implausible_reading_each_poll_sent(observed):
    _assert_clean(_hard_mismatches(observed, _CLAMP_SHARE))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_message_age_reads_each_polls_capture_less_its_header(observed):
    _assert_clean(_hard_mismatches(observed, _MESSAGE_AGE))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_each_day_holds_the_rows_of_the_path_that_reached_it_first_and_is_stamped_with_its_majority(observed):
    _assert_clean(_hard_mismatches(observed, _SOURCES))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_daily_dwell_and_running_times_pair_each_arrival_with_its_departure(observed):
    _assert_clean(_hard_mismatches(observed, _DWELL_DAILY))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_dwell_report_matches_the_truth(observed):
    _assert_clean(_hard_mismatches(observed, _DWELL_REPORT))


@pytest.mark.parametrize("observed", [_TIMETABLE_CHANGE], indirect=True)
def test_headway_quality_judges_each_day_by_the_timetable_in_force_that_day(observed):
    _assert_clean(_divergence_mismatches(observed, _LOADED_TIMETABLE_DIVERGENCE))


@pytest.mark.parametrize("observed", [_PUBLISHED_EARLY], indirect=True)
def test_the_scheduled_headway_of_an_even_count_of_gaps_is_the_mean_of_the_middle_two(observed):
    _assert_clean(_divergence_mismatches(observed, _LOWER_MIDDLE_DIVERGENCE))


@pytest.mark.parametrize("observed", [_CUTOVER], indirect=True)
def test_each_departure_is_filed_under_its_trips_service_day(observed):
    _assert_clean(_divergence_mismatches(observed, _CAPTURE_DAY_DIVERGENCE))


def _at_true_delays(found: oracle.Outcome) -> oracle.Outcome:
    """*found* as an oracle that took the feed's predictions for the truth
    would expect it: every departure at its true delay."""
    return replace(found, departures=tuple(replace(p.departure, delay_sec=p.true_delay_sec) for p in found.predictions))


@pytest.mark.parametrize("observed", [_IMPERFECT], indirect=True)
def test_an_expectation_planted_at_the_true_delays_fails_on_every_cell_the_predictions_move(observed):
    """The run is held to the feed's kept predictions. Held to the true
    delays instead, it fails on exactly the cells the predictions move from
    the truth, the day's on-time and late counts among them, so the
    comparisons see the model's error rather than pass whatever the feed
    predicts."""
    truth = observed.truth
    planted = _at_true_delays(truth)
    moved: set[tuple[str, Hashable]] = set()
    caught: set[tuple[str, Hashable]] = set()
    for expect, read in _COMPARISONS:
        moved |= _moved(expect(observed, truth), expect(observed, planted))
        caught |= set(_unfit(read(observed), expect(observed, planted)))
    day = observed.scenario.days[0].date
    assert ("agg_route_daily_dist", ("P1", day, "平日")) in moved
    assert caught == moved
