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

The service days sit two days before the real JST today. Promotion moves only
closed JST days, so every poll must land on one, the night route's
after-midnight polls included. ``updates_live`` drops a row three days after
its capture, so the polls cannot be older than that either. Day types are
declared with each day, not read from its weekday. A table keyed on the day
of the week is compared under the service date's own weekday, which the
oracle reads from the same date.

Each scenario runs through the pipeline once per module (the ``observed``
fixture), and every test compares that one snapshot with the oracle. Every
scenario's feed stands for one whose status fields a probe has confirmed:
the runner records that verdict by hand, since the scenario feed carries no
arrival times and a live probe would refute the arrival field.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import clickhouse_connect
import httpx
import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport

from api.range import jst_today
from tests.fixtures import scenario_oracle as oracle
from tests.fixtures.scenario import (
    JST,
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
    gtfs_clock,
    gtfs_seconds,
    poll_batches,
    static_gtfs_zip,
)

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


def _runs(route: Route, day_type: str, day: date, spacing_sec: int, delays: dict[str, tuple[int | None, ...]]):
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
# runs. Each route has its own stops, and the routes of each group below own
# their scheduled hours on each day, so a table keyed by route, stop or
# (service date, hour) holds each group's figures apart.
_CONTROL = "C0"
_CANCELLED_LISTING_STOPS = "CL"
_CANCELLED_WITHOUT_STOPS = "CT"
_UNREPORTED = "CS"
_SKIPPED_FROM_THE_START = "CK"
_CANCELLED_AFTER_PREDICTIONS = "CC"
_SKIPPED_AFTER_PREDICTIONS = "CP"
_ROUTES_REPORTED_AT_ONCE = frozenset(
    {_CONTROL, _CANCELLED_LISTING_STOPS, _CANCELLED_WITHOUT_STOPS, _UNREPORTED, _SKIPPED_FROM_THE_START}
)
# Each route runs four trips of eight stops, six minutes apart, from these
# first departures, all of a group's departures inside its hour.
_CANCELLATION_TIMETABLE = {
    (_CONTROL, "平日"): "07:00:00",
    (_CANCELLED_LISTING_STOPS, "平日"): "07:01:00",
    (_CANCELLED_WITHOUT_STOPS, "平日"): "07:02:00",
    (_UNREPORTED, "平日"): "07:03:00",
    (_CANCELLED_AFTER_PREDICTIONS, "平日"): "08:00:00",
    (_CONTROL, "土曜"): "07:00:00",
    (_SKIPPED_FROM_THE_START, "土曜"): "07:01:00",
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

    On the first day, a 平日, routes CL, CT and CS each lose their last trip:
    cancelled with its stops listed, cancelled with none listed, and not run
    without the feed ever mentioning it. CC's last trip is predicted 90 s
    late and then cancelled two minutes before it was due. On the second
    day, a 土曜, CK's last run skips its third stop, reported SKIPPED from
    the start, and CP's skips its third stop too, predicted until the feed
    announces the skip. Every other trip runs, C0's on both days.

    The feed is polled every two minutes."""
    second = day + timedelta(days=1)
    codes = (
        _CONTROL,
        _CANCELLED_LISTING_STOPS,
        _CANCELLED_WITHOUT_STOPS,
        _UNREPORTED,
        _CANCELLED_AFTER_PREDICTIONS,
        _SKIPPED_FROM_THE_START,
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
        last(code, "平日").trip_id
        for code in (_CANCELLED_LISTING_STOPS, _CANCELLED_WITHOUT_STOPS, _UNREPORTED, _CANCELLED_AFTER_PREDICTIONS)
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
            Cancellation(last(_CANCELLED_WITHOUT_STOPS, "平日").trip_id, day, lists_stops=False),
            Cancellation(
                last(_CANCELLED_AFTER_PREDICTIONS, "平日").trip_id,
                day,
                announced_sec=gtfs_seconds("08:16:00"),
                predicted_delay_sec=90,
            ),
        ),
        unreported=(Unreported(last(_UNREPORTED, "平日").trip_id, day),),
        feed=FeedModel(poll_interval_sec=120, lead_sec=300),
    )


def _anchor_day() -> date:
    return jst_today() - timedelta(days=2)


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


def _diff(label: str, expected: dict, actual: dict, *, extra_ok: bool = False) -> list[str]:
    """Each key or cell where *actual* departs from *expected*, as one line.
    Only *expected*'s columns are compared; *extra_ok* lets *actual* hold keys
    *expected* does not name."""
    out = []
    for key in sorted(expected.keys() | actual.keys(), key=str):
        if key not in actual:
            out.append(f"{label} {key}: expected {expected[key]}, missing")
            continue
        if key not in expected:
            if not extra_ok:
                out.append(f"{label} {key}: unexpected row {actual[key]}")
            continue
        for column, want in expected[key].items():
            got = actual[key].get(column)
            if not _matches(want, got):
                out.append(f"{label} {key} {column}: expected {want}, actual {got}")
    return out


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
    """What the pipeline produced for one scenario: each table's rows, a
    ``time`` or an ``interval`` as its ``HH:MM:SS`` text; each route report's
    rows; the trend report's first row; and the council summary of every
    window ``_windows`` names."""

    scenario: Scenario
    tables: dict[str, list[dict]]
    reports: dict[str, list]
    trend: dict[str, Any]
    council: dict[tuple[date, date], dict[str, Any]]


def _window(scenario: Scenario) -> str:
    days = [d.date for d in scenario.days]
    return f"from={min(days)}&to={max(days)}"


def _windows(scenario: Scenario) -> list[tuple[date, date]]:
    """The whole period, then each service day alone."""
    days = sorted(d.date for d in scenario.days)
    return [(days[0], days[-1]), *((d, d) for d in days if len(days) > 1)]


def _keyed(rows: list[dict], columns: tuple[str, ...]) -> dict:
    return {tuple(r[c] for c in columns): r for r in rows}


def _sums(f: oracle.Figures) -> dict:
    return {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec}


Routes = frozenset[str] | None


def _on(routes: Routes):
    """The oracle's filter for departures on *routes*, every route when None."""
    return None if routes is None else (lambda d: d.route_id in routes)


def _rows_on(rows: list[dict], routes: Routes) -> list[dict]:
    return rows if routes is None else [r for r in rows if r["route_code"] in routes]


def _of_routes(expected: dict, routes: Routes) -> dict:
    """*expected* keyed with its route first, kept to *routes*."""
    return expected if routes is None else {k: v for k, v in expected.items() if k[0] in routes}


def _daily_mismatches(observed: Observed, routes: Routes = None) -> list[str]:
    days = _of_routes(oracle.by_route_day(observed.scenario), routes)
    key = ("route_code", "date", "service_type")
    tables = observed.tables
    return (
        _diff(
            "agg_route_daily_dist",
            {k: {**_sums(f), "on_time_count": f.on_time, "late5_count": f.over_five_min} for k, f in days.items()},
            _keyed(_rows_on(tables["agg_route_daily_dist"], routes), key),
        )
        + _diff(
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
            _keyed(_rows_on(tables["agg_route_daily"], routes), key),
        )
        + _diff(
            "agg_daily_trend",
            {k: {**_sums(f), "avg_min": _mean_min(f)} for k, f in days.items()},
            _keyed(_rows_on(tables["agg_daily_trend"], routes), key),
        )
    )


def _stop_mismatches(observed: Observed, routes: Routes = None) -> list[str]:
    """Per stop, service date and day type, its time bands summed. The stop
    comes from the timetable each row was ingested against, kept in the
    archive once a revision replaces it."""
    scenario = observed.scenario
    route_of = {stop.stop_id: route.route_id for route in scenario.routes for stop in route.stops}
    expected = {
        k: {"samples": f.departures, "delay_sum": f.delay_sum_sec}
        for k, f in oracle.by_stop_day(scenario, _on(routes)).items()
    }
    actual: dict = {}
    for row in observed.tables["agg_stop_daily"]:
        if routes is not None and route_of.get(row["stop_id"]) not in routes:
            continue
        cell = actual.setdefault((row["stop_id"], row["date"], row["service_type"]), {"samples": 0, "delay_sum": 0})
        cell["samples"] += row["samples"]
        cell["delay_sum"] += row["delay_sum"]
    return _diff("agg_stop_daily, time bands summed", expected, actual)


def _pooled_mismatches(observed: Observed, routes: Routes = None) -> list[str]:
    expected = {
        k: {
            **_sums(f),
            "avg_min": _mean_min(f),
            "on_time_pct": _pct(f.on_time_share),
            "late5_pct": _pct(f.over_five_min_share),
            "late_5min_plus": f.over_five_min,
        }
        for k, f in _of_routes(oracle.by_route_day_type(observed.scenario), routes).items()
    }
    return _diff(
        "agg_route_stats",
        expected,
        _keyed(_rows_on(observed.tables["agg_route_stats"], routes), ("route_code", "service_type")),
    )


def _report_mismatches(observed: Observed, routes: Routes = None) -> list[str]:
    window = _window(observed.scenario)
    by_route = oracle.by_route(observed.scenario)
    kept = {route: f for route, f in by_route.items() if routes is None or route in routes}

    def ours(rows: list) -> list:
        return [r for r in rows if routes is None or r[0] in routes]

    # on_time rows: (route_code, service, on_time_pct, avg_min, samples, ...)
    # route_table rows: (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, early_pct, ...)
    on_time = {r[0]: {"on_time_pct": r[2], "avg_min": r[3], "samples": r[4]} for r in ours(observed.reports["on_time"])}
    table = {
        r[0]: {
            "avg_min": r[1],
            "on_time_pct": r[2],
            "late5_pct": r[3],
            "late5_count": r[4],
            "samples": r[5],
            "early_pct": r[6],
        }
        for r in ours(observed.reports["route_table"])
    }
    industry = {r[0]: {"on_time_pct": r[2], "early_pct": r[6]} for r in ours(observed.reports["route_table_industry"])}
    return (
        _diff(
            f"GET reports/on_time {window}",
            {
                route: {"on_time_pct": _pct(f.on_time_share), "avg_min": _mean_min(f), "samples": f.departures}
                for route, f in kept.items()
            },
            on_time,
        )
        + _diff(
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
                for route, f in kept.items()
            },
            table,
        )
        + _diff(
            f"GET reports/route_table {window}&preset=standard_m1_p5",
            {
                route: {"on_time_pct": _pct(f.on_time_industry_share), "early_pct": _pct(f.early_share)}
                for route, f in kept.items()
            },
            industry,
        )
    )


def _after_midnight(d: oracle.Departure) -> bool:
    return not oracle.before_midnight(d)


def _hours_of(scenario: Scenario, routes: Routes) -> set[tuple[date, int]]:
    """The (service date, hour) cells *routes* hold a scheduled departure in,
    every route's when None. A cell another route also holds is refused: a
    table keyed only by date and hour could not tell the two apart."""

    def cells(keep) -> set[tuple[date, int]]:
        return {
            (day.date, sec // 3600)
            for day in scenario.days
            for trip in scenario.scheduled(day.date)
            if keep(trip.route_id)
            for sec in trip.scheduled_sec
        }

    if routes is None:
        return cells(lambda _route: True)
    ours, others = cells(lambda r: r in routes), cells(lambda r: r not in routes)
    if ours & others:
        raise RuntimeError(f"routes {sorted(routes)} share hours {sorted(ours & others)} with other routes")
    return ours


def _hourly_mismatches(observed: Observed, *, after: bool, routes: Routes = None) -> list[str]:
    """The departures on one side of 24:00 (*after* it, or before), bucket by
    bucket under the oracle's hour, weekday and scheduled time, against the
    rows of each hourly table on the same side. A departure after midnight
    belongs to its service day's hour 24, 25, ..."""
    scenario = observed.scenario
    on_routes = _on(routes)
    side_of = _after_midnight if after else oracle.before_midnight

    def where(d: oracle.Departure) -> bool:
        return side_of(d) and (on_routes is None or on_routes(d))

    label = "departures at 24:00 or later" if after else "departures before 24:00"
    tables = observed.tables
    hours = _hours_of(scenario, routes)

    def side(rows: list[dict], is_after) -> list[dict]:
        return [r for r in rows if is_after(r) == after]

    return (
        _diff(
            f"agg_hour_daily, {label}",
            {k: _sums(f) for k, f in oracle.by_day_hour(scenario, where).items()},
            _keyed(
                [
                    r
                    for r in side(tables["agg_hour_daily"], lambda r: r["hour"] >= 24)
                    if (r["date"], r["hour"]) in hours
                ],
                ("date", "hour"),
            ),
        )
        + _diff(
            f"agg_route_hour, {label}",
            {
                (route, day_type, gtfs_clock(sec)): _sums(f)
                for (route, day_type, sec), f in oracle.by_route_day_type_time(scenario, where).items()
            },
            _keyed(
                side(_rows_on(tables["agg_route_hour"], routes), lambda r: r["scheduled_time"] >= "24:00:00"),
                ("route_code", "service_type", "scheduled_time"),
            ),
        )
        + _diff(
            f"agg_route_hour_dow, {label}",
            {k: _sums(f) for k, f in oracle.by_route_day_type_weekday_hour(scenario, where).items()},
            _keyed(
                side(_rows_on(tables["agg_route_hour_dow"], routes), lambda r: r["hour"] >= 24),
                ("route_code", "service_type", "dow", "hour"),
            ),
        )
    )


def _delay_mismatches(observed: Observed, routes: Routes = None) -> list[str]:
    """Every delay figure of *routes*: the daily, stop, pooled and hourly
    tables and the route reports."""
    return (
        _daily_mismatches(observed, routes)
        + _stop_mismatches(observed, routes)
        + _pooled_mismatches(observed, routes)
        + _report_mismatches(observed, routes)
        + _hourly_mismatches(observed, after=False, routes=routes)
        + _hourly_mismatches(observed, after=True, routes=routes)
    )


def _council_label(window: tuple[date, date]) -> str:
    return f"GET reports/council_summary from={window[0]}&to={window[1]}"


def _timetable_mismatches(observed: Observed) -> list[str]:
    """Each day's timetable version and planned trips, each version's trip
    count, the trend report's revision boundaries, and the council summary's
    planned trips and the days they cover."""
    scenario = observed.scenario
    tables = observed.tables
    service = oracle.service(scenario)
    versions = oracle.timetable_versions(scenario)
    out = (
        _diff(
            "agg_schedule_revision_daily",
            {(day,): {"static_version_id": name} for day, name in versions.items()},
            _keyed(tables["agg_schedule_revision_daily"], ("date",)),
        )
        + _diff(
            "agg_static_version_summary",
            {(name,): {"trip_count": trips} for name, trips in oracle.version_trips(scenario).items()},
            _keyed(tables["agg_static_version_summary"], ("static_version_id",)),
        )
        + _diff(
            "planned_trips_daily",
            {(day,): {"planned_trips": s.planned} for day, s in service.items()},
            _keyed(tables["planned_trips_daily"], ("service_date",)),
        )
    )
    boundaries = [
        day.isoformat() for day, name in versions.items() if versions.get(day - timedelta(days=1), name) != name
    ]
    if observed.trend["revision_boundaries"] != boundaries:
        out.append(
            f"GET reports/trend {_window(scenario)} revision_boundaries: "
            f"expected {boundaries}, actual {observed.trend['revision_boundaries']}"
        )
    for window, council in observed.council.items():
        days = [day for day in service if window[0] <= day <= window[1]]
        out += _diff(
            _council_label(window),
            {
                window: {
                    "planned_trips": sum(service[day].planned for day in days),
                    "covered_days": len(days),
                    "window_days": (window[1] - window[0]).days + 1,
                }
            },
            {window: council},
        )
    return out


def _service_mismatches(observed: Observed, days: set[date], windows: list[tuple[date, date]]) -> list[str]:
    """The trips reported cancelled on each of *days*, and the council
    summary's executed trips and service-delivered rate over each of
    *windows*. The rate is shown only once the feed has reported a
    cancellation, so a feed that never sends the status reads "not
    available" rather than a 100% that measured nothing."""
    service = oracle.service(observed.scenario)
    out = _diff(
        "agg_service_delivered_daily",
        {
            (day,): {"non_executed_trips": s.reported_cancelled}
            for day, s in service.items()
            if day in days and s.reported_cancelled
        },
        _keyed([r for r in observed.tables["agg_service_delivered_daily"] if r["date"] in days], ("date",)),
    )
    for window in windows:
        in_window = [s for day, s in service.items() if window[0] <= day <= window[1]]
        planned, delivered = sum(s.planned for s in in_window), sum(s.delivered for s in in_window)
        if any(s.reported_cancelled for day, s in service.items() if day <= window[1]):
            expected = {
                "executed_trips": delivered,
                "service_delivered_pct": _pct(Fraction(delivered, planned)),
                "delivered_missing": None,
            }
        else:
            expected = {
                "executed_trips": None,
                "service_delivered_pct": None,
                "delivered_missing": "feed_reports_no_cancellations",
            }
        out += _diff(_council_label(window), {window: expected}, {window: observed.council[window]})
    return out


def _closed_and_live(scenario_polls, today: date) -> None:
    """Every poll must sit on a closed JST day, which promotion moves, and
    inside updates_live's three days, or the pipeline never sees it."""
    if not scenario_polls:
        return
    today_start = datetime.combine(today, time(0), tzinfo=JST)
    if max(p.captured_at for p in scenario_polls) >= today_start:
        raise RuntimeError("a poll falls on today, which is not closed")
    if min(p.captured_at for p in scenario_polls) <= datetime.now(JST) - timedelta(days=3) + timedelta(hours=1):
        raise RuntimeError("a poll is near updates_live's TTL")


async def _run_scenario(scenario: Scenario, pg_conn, ch_client, tmp_dir: Path) -> int:
    """Load, ingest, promote and analyze *scenario* for a new agency; return its id.

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
    _closed_and_live([poll for _version, batch in batches for poll in batch], jst_today())
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
    return agency_id


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
    return {
        "planned_trips": row[3],
        "executed_trips": row[4],
        "service_delivered_pct": row[5],
        "covered_days": response["covered_days"],
        "window_days": response["window_days"],
        "delivered_missing": response["delivered_missing"],
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

    scenario = request.param(_anchor_day())
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
            agency_id = await _run_scenario(scenario, pg_conn, ch_client, tmp_path_factory.mktemp(scenario.name))
            tables = _read_tables(pg_conn, agency_id)
            pool = await _test_pool()
            stack.push_async_callback(pool.close)
            await confirm_rt_field_coverage(pool, agency_id)
            app.state.pool, app.state.ch_client = pool, ch_async
            reports, trend, council = await _read_reports(scenario, agency_id)
    except AssertionError as exc:
        raise RuntimeError(f"scenario {scenario.name}: an assertion failed while the pipeline ran") from exc
    return Observed(scenario, tables, reports, trend, council)


def _assert_clean(mismatches: list[str]) -> None:
    assert not mismatches, "pipeline differs from the truth:\n" + "\n".join(mismatches)


# Every test parametrizes `observed` with these same param objects. pytest
# keeps a module-scoped fixture's value per parameter value and runs the tests
# that share one together, so each scenario's pipeline runs once for the
# module. The cancellations scenario holds several cases the pipeline may
# treat differently, so its tests compare its routes and days in groups that
# each hold one case.
_PLAIN = pytest.param(plain_weekday, id="plain_weekday")
_NIGHT_ROUTE = pytest.param(night_route, id="night_route")
_TIMETABLE_CHANGE = pytest.param(timetable_change, id="timetable_change")
_TIMETABLE_PUBLISHED_EARLY = pytest.param(timetable_published_early, id="timetable_published_early")
_CANCELLATIONS = pytest.param(cancellations, id="cancellations")
_SCENARIOS = [_PLAIN, _NIGHT_ROUTE, _TIMETABLE_CHANGE]
_ALL = [*_SCENARIOS, _CANCELLATIONS]


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_daily_and_pooled_aggregates_match_the_truth(observed):
    _assert_clean(_daily_mismatches(observed) + _stop_mismatches(observed) + _pooled_mismatches(observed))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_reports_match_the_truth(observed):
    _assert_clean(_report_mismatches(observed))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_hourly_tables_file_each_departure_before_midnight_under_its_scheduled_hour(observed):
    _assert_clean(_hourly_mismatches(observed, after=False))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_hourly_tables_hold_the_departures_after_midnight(observed):
    _assert_clean(_hourly_mismatches(observed, after=True))


@pytest.mark.parametrize("observed", _ALL, indirect=True)
def test_each_day_is_planned_and_filed_under_the_timetable_in_force(observed):
    _assert_clean(_timetable_mismatches(observed))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_service_delivered_matches_the_truth(observed):
    service_days = {d.date for d in observed.scenario.days}
    _assert_clean(_service_mismatches(observed, service_days, _windows(observed.scenario)))


@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_runs_reported_as_they_happened_beside_cancelled_unreported_and_skipping_trips_match_the_truth(observed):
    _assert_clean(_delay_mismatches(observed, _ROUTES_REPORTED_AT_ONCE))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A trip the feed predicted and then reported CANCELED before it left keeps its last predictions as "
        "departures: the delay dedup drops rows without a delay before it picks each stop's latest poll, so "
        "the CANCELED polls never replace the predictions, and every delay table and route report counts the "
        "cancelled trip's stops as departures and the trip as observed."
    ),
)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_trip_cancelled_after_it_was_predicted_has_no_departures(observed):
    _assert_clean(_delay_mismatches(observed, frozenset({_CANCELLED_AFTER_PREDICTIONS})))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A stop the feed predicted and then reported SKIPPED keeps its last prediction as a departure: the "
        "delay dedup drops the SKIPPED rows, which carry no delay, before it picks the stop's latest poll."
    ),
)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_stop_skipped_after_it_was_predicted_has_no_departure(observed):
    _assert_clean(_delay_mismatches(observed, frozenset({_SKIPPED_AFTER_PREDICTIONS})))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A CANCELED TripUpdate that lists no StopTimeUpdate, which GTFS-RT allows, stores nothing: ingest "
        "writes one row per StopTimeUpdate, so agg_service_delivered_daily never counts that cancellation."
    ),
)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_a_trip_cancelled_without_stop_updates_counts_as_reported_cancelled(observed):
    first = min(d.date for d in observed.scenario.days)
    _assert_clean(_service_mismatches(observed, {first}, []))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Definition conflict: agg_service_delivered_daily counts a trip whose latest status at any stop is "
        "SKIPPED as not executed, so a run that served its other stops lowers executed trips and the "
        "service-delivered rate. The oracle counts a run that skipped stops as delivered."
    ),
)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_runs_that_skipped_stops_count_as_delivered(observed):
    second = max(d.date for d in observed.scenario.days)
    _assert_clean(_service_mismatches(observed, {second}, [(second, second)]))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Executed trips are the planned trips minus those reported not run, so a planned trip the feed never "
        "reported counts as executed (a definition conflict), and so does the trip cancelled without stop "
        "updates, which ingest never stores."
    ),
)
@pytest.mark.parametrize("observed", [_CANCELLATIONS], indirect=True)
def test_trips_that_did_not_run_are_not_counted_as_executed(observed):
    # Compared day by day: errors of opposite sign on two days would offset
    # over the whole period.
    first = min(d.date for d in observed.scenario.days)
    _assert_clean(_service_mismatches(observed, set(), [(first, first)]))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Ingest joins each poll to the timetable loaded when it arrives, not to the one in force on its "
        "service date, so the polls of a day before a revision's first day that arrive after the revision is "
        "loaded take its schedule: a trip it moved is filed under its new times, a trip it dropped loses its "
        "day type and its stops, and the day is recorded as running the revision."
    ),
)
@pytest.mark.parametrize("observed", [_TIMETABLE_PUBLISHED_EARLY], indirect=True)
def test_a_revision_loaded_before_its_first_day_leaves_the_days_before_it_on_their_own_timetable(observed):
    _assert_clean(_delay_mismatches(observed) + _timetable_mismatches(observed))
