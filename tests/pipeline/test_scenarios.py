"""Scenarios with known truth, pushed through the real ingest path.

Each scenario (``tests.fixtures.scenario``) declares an operation and its
truth. Its static GTFS loads through ``load_static``, and each of its
TripUpdate polls is POSTed to the collector's ingest endpoint. Promotion then
moves the closed days into ``updates``, and ``analyze`` builds the aggregates.
The aggregates and the report endpoints are compared with
``tests.fixtures.scenario_oracle``, which computes the same figures from the
truth alone.

A scenario whose timetable is revised loads each version at the instant it
declares, as an operator loads a newly published zip, and ingests each poll
against the version loaded when it was captured. After each version's polls
the runner promotes and analyzes, as the daily jobs would before the next
version arrives, so the run that sees the new version updates aggregates
built under the old one.

The service days sit two days before the service day in progress, and every
poll must land on a closed JST day and outlast the run in ``updates_live``
(``tests.fixtures.scenario_clock``). Day types are declared with each day,
not read from its weekday. A table keyed on the day of the week is compared
under the service date's own weekday, which the oracle reads from the same
date.

Each scenario runs through the pipeline once per module (the ``observed``
fixture), and every test compares that one snapshot with the oracle. Every
scenario's feed stands for one whose status fields a probe has confirmed:
the runner records that verdict by hand, since the scenario feed carries no
arrival times and a live probe would refute the arrival field.
"""

from __future__ import annotations

import contextlib
import itertools
import os
from collections import Counter
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from functools import cached_property, partial
from pathlib import Path
from typing import Any, cast

import clickhouse_connect
import httpx
import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport

from tests.fixtures import scenario_oracle as oracle
from tests.fixtures.scenario import (
    JST,
    Cancellation,
    FeedModel,
    Poll,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    StopEntry,
    TimetableVersion,
    TripRun,
    Unreported,
    gtfs_clock,
    gtfs_seconds,
    poll_batches,
    static_gtfs_zip,
)
from tests.fixtures.scenario_clock import anchor_day, check_closed_and_live

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
# runs. Each route has its own stops, and the routes whose figures a known
# divergence moves own their scheduled hours, so the cells it moves hold
# nothing else.
_CONTROL = "C0"
_CANCELLED_LISTING_STOPS = "CL"
_CANCELLED_WITHOUT_STOPS = "CT"
_CANCELLED_AFTER_PREDICTIONS = "CC"
_UNREPORTED = "CS"
_EVERY_STOP_SKIPPED = "CA"
_SKIPPED_FROM_THE_START = "CK"
_SKIPPED_AFTER_PREDICTIONS = "CP"
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
    On the second day, a 土曜, CA's last trip does not run and the feed
    reports every stop of it SKIPPED, and CS's last trip does not run without
    the feed ever mentioning it. CK's last run skips its third stop, reported
    SKIPPED from the start, and CP's skips its third stop too, predicted
    until the feed announces the skip. Every other trip runs, C0's on both
    days.

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
            for code in (_CANCELLED_LISTING_STOPS, _CANCELLED_WITHOUT_STOPS, _CANCELLED_AFTER_PREDICTIONS)
        ),
        *(last(code, "土曜").trip_id for code in (_EVERY_STOP_SKIPPED, _UNREPORTED)),
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
            Cancellation(last(_EVERY_STOP_SKIPPED, "土曜").trip_id, second, reported_as="every_stop_skipped"),
        ),
        unreported=(Unreported(last(_UNREPORTED, "土曜").trip_id, second),),
        feed=FeedModel(poll_interval_sec=120, lead_sec=300),
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


def _matches(expected, actual) -> bool:
    if isinstance(expected, Rounded):
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
    "agg_service_delivered_daily": "date, non_executed_trips",
}
# Each read's report and query. The industry window puts readings exactly a
# minute early and exactly five minutes late on its edges.
_REPORTS = {
    "on_time": ("on_time", ""),
    "route_table": ("route_table", "&include_sparse=1"),
    "route_table_industry": ("route_table", "&include_sparse=1&preset=standard_m1_p5"),
}


@dataclass(frozen=True)
class Observed:
    """One scenario's run: the polls ingested under each timetable version,
    and what the pipeline produced: each table's rows, a ``time`` or an
    ``interval`` as its ``HH:MM:SS`` text; each route report's rows; the trend
    report's first row; and the council summary of every window ``_windows``
    names."""

    scenario: Scenario
    batches: list[tuple[TimetableVersion, list[Poll]]]
    tables: dict[str, list[dict]]
    reports: dict[str, list]
    trend: dict[str, Any]
    council: dict[tuple[date, date], dict[str, Any]]

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
    window = _window(observed.scenario)
    routes = oracle.by_route(found)
    return (
        _cells(
            f"GET reports/on_time {window}",
            {
                route: {"on_time_pct": _pct(f.on_time_share), "avg_min": _mean_min(f), "samples": f.departures}
                for route, f in routes.items()
            },
        )
        | _cells(
            f"GET reports/route_table {window}",
            {
                route: {
                    "avg_min": _mean_min(f),
                    "on_time_pct": _pct(f.on_time_share),
                    "late5_pct": _pct(f.over_five_min_share),
                    "late5_count": f.over_five_min,
                    "samples": f.departures,
                    "early_pct": _pct(f.early_share),
                }
                for route, f in routes.items()
            },
        )
        | _cells(
            f"GET reports/route_table {window}&preset=standard_m1_p5",
            {
                route: {"on_time_pct": _pct(f.on_time_industry_share), "early_pct": _pct(f.early_share)}
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
        | _cells(
            f"GET reports/route_table {window}",
            {
                r[0]: {
                    "avg_min": r[1],
                    "on_time_pct": r[2],
                    "late5_pct": r[3],
                    "late5_count": r[4],
                    "samples": r[5],
                    "early_pct": r[6],
                }
                for r in reports["route_table"]
            },
        )
        | _cells(
            f"GET reports/route_table {window}&preset=standard_m1_p5",
            {r[0]: {"on_time_pct": r[2], "early_pct": r[6]} for r in reports["route_table_industry"]},
        )
    )


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
            {
                (day,): {"non_executed_trips": s.reported_not_run}
                for day, s in found.service.items()
                if s.reported_not_run
            },
        )
        | _cells(_council_label("service delivered"), delivered)
        | _cells(_council_label("coverage"), coverage)
    )


def _service_actual(observed: Observed) -> Cells:
    return (
        _keyed("agg_service_delivered_daily", observed.tables["agg_service_delivered_daily"], ("date",))
        | _cells(_council_label("service delivered"), observed.council)
        | _cells(_council_label("coverage"), observed.council)
    )


Expect = Callable[[Observed, oracle.Outcome], Cells]
# Each test's comparison: the cells the oracle expects of an outcome, and the
# cells the run produced.
_DAILY = (_daily_expected, _daily_actual)
_REPORTS_COMPARED = (_reports_expected, _reports_actual)
_BEFORE_MIDNIGHT = (partial(_hourly_expected, after=False), partial(_hourly_actual, after=False))
_AFTER_MIDNIGHT = (partial(_hourly_expected, after=True), partial(_hourly_actual, after=True))
_TIMETABLE = (_timetable_expected, _timetable_actual)
_SERVICE = (_service_expected, _service_actual)
_COMPARISONS = (_DAILY, _REPORTS_COMPARED, _BEFORE_MIDNIGHT, _AFTER_MIDNIGHT, _TIMETABLE, _SERVICE)


@dataclass(frozen=True)
class Divergence:
    """A known way the pipeline departs from the truth. ``view`` turns an
    outcome into the outcome the pipeline computes instead, from the run's
    scenario and polls; ``reason`` states the divergence as its strict xfail
    does, and ``name`` names it in a mismatch report."""

    name: str
    reason: str
    view: Callable[[Observed, oracle.Outcome], oracle.Outcome]


def _last_predictions(observed: Observed) -> dict[tuple[str, date, int], StopEntry]:
    """Each stop event's latest poll entry that predicted a departure delay."""
    latest = {}
    for _version, batch in observed.batches:
        for poll in batch:
            for trip in poll.trips:
                for entry in trip.stops:
                    if entry.departure_delay is not None:
                        latest[(trip.trip_id, trip.service_date, entry.stop_sequence)] = entry
    return latest


def _stale_departure(scenario: Scenario, trip_id: str, day: date, entry: StopEntry) -> oracle.Departure:
    trip = scenario.trip(trip_id, day)
    return oracle.Departure(
        trip.route_id,
        trip_id,
        day,
        trip.day_type,
        entry.stop_sequence,
        entry.stop_id,
        trip.scheduled_sec[entry.stop_sequence - 1],
        cast(int, entry.departure_delay),
    )


def _with_service(found: oracle.Outcome, change: Callable[[date, oracle.Service], oracle.Service]) -> oracle.Outcome:
    return replace(found, service={day: change(day, s) for day, s in found.service.items()})


def _cancelled_trips_keep_their_predictions(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """Each stop of a trip reported not running keeps the latest delay the feed
    predicted for it as a departure, and the trip counts as observed."""
    scenario = observed.scenario
    latest = _last_predictions(observed)
    stale = [
        _stale_departure(scenario, c.trip_id, c.service_date, entry)
        for c in scenario.cancellations
        for (trip_id, day, _seq), entry in latest.items()
        if (trip_id, day) == (c.trip_id, c.service_date)
    ]
    seen = Counter(day for _trip_id, day in {(d.trip_id, d.service_date) for d in stale})
    found = replace(found, departures=(*found.departures, *stale))
    return _with_service(found, lambda day, s: replace(s, observed=s.observed + seen[day]))


def _skipped_stops_keep_their_predictions(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """Each stop a run skipped keeps the latest delay the feed predicted for it
    as a departure."""
    scenario = observed.scenario
    latest = _last_predictions(observed)
    stale = [
        _stale_departure(scenario, run.trip_id, run.service_date, latest[(run.trip_id, run.service_date, i + 1)])
        for run in scenario.runs
        for i, sec in enumerate(run.actual_sec)
        if sec is None and (run.trip_id, run.service_date, i + 1) in latest
    ]
    return replace(found, departures=(*found.departures, *stale))


def _cancellations_without_stops_are_lost(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """A trip reported CANCELED with no stop listed is not counted as not run."""
    lost = Counter(c.service_date for c in observed.scenario.cancellations if c.reported_as == "canceled_without_stops")
    return _with_service(found, lambda day, s: replace(s, reported_not_run=s.reported_not_run - lost[day]))


def _partial_runs_count_as_not_run(observed: Observed, found: oracle.Outcome) -> oracle.Outcome:
    """A run that skipped some of its stops counts as not run."""
    partial_runs = Counter(r.service_date for r in observed.scenario.runs if None in r.actual_sec)
    return _with_service(found, lambda day, s: replace(s, reported_not_run=s.reported_not_run + partial_runs[day]))


_CANCELLED_AFTER_PREDICTIONS_DIVERGENCE = Divergence(
    name="a cancelled trip keeps its predictions",
    reason=(
        "A trip the feed predicted and then reported CANCELED before it left keeps its last predictions as "
        "departures: the delay dedup drops rows without a delay before it picks each stop's latest poll, so "
        "the CANCELED polls never replace the predictions. Every delay table and route report counts the "
        "cancelled trip's stops as departures, and the trip counts as observed in its day's coverage."
    ),
    view=_cancelled_trips_keep_their_predictions,
)
_SKIPPED_AFTER_PREDICTIONS_DIVERGENCE = Divergence(
    name="a skipped stop keeps its prediction",
    reason=(
        "A stop the feed predicted and then reported SKIPPED keeps its last prediction as a departure: the "
        "delay dedup drops the SKIPPED rows, which carry no delay, before it picks the stop's latest poll."
    ),
    view=_skipped_stops_keep_their_predictions,
)
_CANCELLED_WITHOUT_STOPS_DIVERGENCE = Divergence(
    name="a cancellation without stops is lost",
    reason=(
        "A CANCELED TripUpdate that lists no StopTimeUpdate, which GTFS-RT allows, stores nothing: ingest "
        "writes one row per StopTimeUpdate, so neither agg_service_delivered_daily nor the council summary's "
        "executed trips count that cancellation."
    ),
    view=_cancellations_without_stops_are_lost,
)
_PARTIAL_RUN_DIVERGENCE = Divergence(
    name="a partial run counts as not run",
    reason=(
        "A trip is not run when it is CANCELED or every stop it reports is SKIPPED; a run that skipped only "
        "some stops ran, so it counts as executed. agg_service_delivered_daily instead counts a trip as not "
        "executed when the latest status of any one of its stops is SKIPPED, which lowers executed trips and "
        "the service-delivered rate by every partial run."
    ),
    view=_partial_runs_count_as_not_run,
)
# The divergences each scenario is known to meet, by scenario name.
_KNOWN = {
    "cancellations": (
        _CANCELLED_WITHOUT_STOPS_DIVERGENCE,
        _CANCELLED_AFTER_PREDICTIONS_DIVERGENCE,
        _SKIPPED_AFTER_PREDICTIONS_DIVERGENCE,
        _PARTIAL_RUN_DIVERGENCE,
    ),
}


def _outcomes(observed: Observed) -> list[tuple[tuple[Divergence, ...], oracle.Outcome]]:
    """The truth's outcome, then its view under each combination of the
    scenario's known divergences, each with the divergences it applies."""
    divergences = _KNOWN.get(observed.scenario.name, ())
    truth = oracle.outcome(observed.scenario)
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


def _compare(actual: Cells, expected: Cells, keep: Callable[[tuple[str, Hashable]], bool]) -> list[str]:
    """Each kept cell where *actual* does not fit *expected*, as one line. A
    cell *expected* lacks expects no row."""
    lines = []
    for cell in sorted(set(actual) | set(expected), key=str):
        want, got = expected.get(cell), actual.get(cell)
        if keep(cell) and not _fits(want, got):
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
    others = [d for d in _KNOWN[observed.scenario.name] if d is not divergence]
    truth = oracle.outcome(observed.scenario)
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
        raise RuntimeError("the divergence moves no cell of its own, so its xfail would test nothing")
    return lines


async def _run_scenario(
    scenario: Scenario, pg_conn, ch_client, tmp_dir: Path, now: datetime
) -> tuple[int, list[tuple[TimetableVersion, list[Poll]]]]:
    """Load, ingest, promote and analyze *scenario* for a new agency; return
    its id and the polls ingested under each timetable version.

    Each timetable version is loaded as ``refresh_static`` loads a fetched
    zip, its polls are ingested, and the closed days are promoted and
    analyzed before the next version is loaded."""
    from api.main import app
    from pipeline import day_types, route_catalog, service_hours
    from pipeline.analyze import analyze
    from pipeline.promote import promote_closed_days
    from pipeline.static_loader import load_static

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (f"scenario {scenario.name}", f"https://{scenario.name}.example.com/trip_updates.pb"),
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()

    batches = poll_batches(scenario)
    check_closed_and_live([poll.captured_at for _version, batch in batches for poll in batch], now)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for version, batch in batches:
            zip_path = tmp_dir / f"{version.name}.zip"
            zip_path.write_bytes(static_gtfs_zip(scenario, version))
            load_static(str(zip_path), agency_id, pg_conn, ch_client=ch_client)
            day_types.invalidate_day_types()
            route_catalog.invalidate_retired_routes()
            service_hours.invalidate_service_hours()
            for poll in batch:
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
                if resp.status_code != 200 or resp.json()["inserted"] != poll.stop_updates:
                    raise RuntimeError(
                        f"{poll.source_file} ({poll.stop_updates} updates): {resp.status_code} {resp.text}"
                    )
            promote_closed_days(agency_id, pg_conn, ch_client)
            analyze(agency_id, pg_conn, ch_client)
            pg_conn.commit()
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
    }


async def _read_reports(scenario: Scenario, agency_id: int) -> tuple[dict[str, list], dict, dict]:
    from api.main import app

    async def get(client, report: str, query: str) -> dict:
        resp = await client.get(f"/api/{agency_id}/reports/{report}?{query}")
        if resp.status_code != 200:
            raise RuntimeError(f"reports/{report}?{query}: {resp.status_code} {resp.text}")
        return resp.json()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reports = {
            name: (await get(client, report, f"{_window(scenario)}{query}"))["rows"]
            for name, (report, query) in _REPORTS.items()
        }
        trend = (await get(client, "trend", _window(scenario)))["rows"][0]
        council = {
            window: _council_figures(await get(client, "council_summary", f"from={window[0]}&to={window[1]}"))
            for window in _windows(scenario)
        }
    return reports, trend, council


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
            reports, trend, council = await _read_reports(scenario, agency_id)
    except AssertionError as exc:
        raise RuntimeError(f"scenario {scenario.name}: an assertion failed while the pipeline ran") from exc
    return Observed(scenario, batches, tables, reports, trend, council)


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
# fixing it turns that test into a pass.
_ALL = [
    pytest.param(plain_weekday, id="plain_weekday"),
    pytest.param(night_route, id="night_route"),
    pytest.param(timetable_change, id="timetable_change"),
    pytest.param(timetable_published_early, id="timetable_published_early"),
    _CANCELLATIONS := pytest.param(cancellations, id="cancellations"),
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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_CANCELLED_AFTER_PREDICTIONS_DIVERGENCE.reason)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_trip_cancelled_after_it_was_predicted_has_no_departures(observed):
    _assert_clean(_divergence_mismatches(observed, _CANCELLED_AFTER_PREDICTIONS_DIVERGENCE))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_SKIPPED_AFTER_PREDICTIONS_DIVERGENCE.reason)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_stop_skipped_after_it_was_predicted_has_no_departure(observed):
    _assert_clean(_divergence_mismatches(observed, _SKIPPED_AFTER_PREDICTIONS_DIVERGENCE))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_CANCELLED_WITHOUT_STOPS_DIVERGENCE.reason)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_trip_cancelled_without_stop_updates_counts_as_not_run(observed):
    _assert_clean(_divergence_mismatches(observed, _CANCELLED_WITHOUT_STOPS_DIVERGENCE))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_PARTIAL_RUN_DIVERGENCE.reason)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_run_that_skipped_some_stops_counts_as_executed(observed):
    _assert_clean(_divergence_mismatches(observed, _PARTIAL_RUN_DIVERGENCE))
