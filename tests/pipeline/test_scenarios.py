"""Scenarios with known truth, pushed through the real ingest path.

Each scenario (``tests.fixtures.scenario``) declares an operation and its
truth. Its static GTFS loads through ``load_static``, and each of its
TripUpdate polls is POSTed to the collector's ingest endpoint. Promotion then
moves the closed days into ``updates``, and ``analyze`` builds the aggregates.
The aggregates and the report endpoints are compared with
``tests.fixtures.scenario_oracle``, which computes the same figures from the
truth alone.

The service days sit two days before the real JST today. Promotion moves only
closed JST days, so every poll must land on one, the night route's
after-midnight polls included. ``updates_live`` drops a row three days after
its capture, so the polls cannot be older than that either. Day types are
declared with each day, not read from its weekday. A table keyed on the day
of the week is compared under the service date's own weekday, which the
oracle reads from the same date.

Each scenario runs through the pipeline once per module (the ``observed``
fixture), and every test compares that one snapshot with the oracle.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from pathlib import Path

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
    FeedModel,
    Route,
    Scenario,
    ScheduledTrip,
    ServiceDay,
    Stop,
    TripRun,
    gtfs_clock,
    gtfs_seconds,
    polls,
    static_gtfs_zip,
)

_COLLECTOR_SECRET = "scenario-secret"


def _route(route_id: str, name: str, n_stops: int, lat: float) -> Route:
    stops = tuple(
        Stop(f"{route_id}_S{i}", f"{name} {i}", round(lat + i * 0.002, 6), round(132.45 + i * 0.002, 6))
        for i in range(1, n_stops + 1)
    )
    return Route(route_id, name, stops)


def _trip(route: Route, day_type: str, first: str, spacing_sec: int) -> ScheduledTrip:
    start = gtfs_seconds(first)
    departures = tuple(gtfs_clock(start + i * spacing_sec) for i in range(len(route.stops)))
    return ScheduledTrip(f"{route.route_id}_{first[:5].replace(':', '')}", route.route_id, day_type, departures)


def _runs(route: Route, day_type: str, day: date, spacing_sec: int, delays: dict[str, tuple[int, ...]]):
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
}
_REPORTS = {"on_time": "", "route_table": "&include_sparse=1"}


@dataclass(frozen=True)
class Observed:
    """What the pipeline produced for one scenario: each table's rows, a
    ``time`` as its ``HH:MM:SS`` text, and each report's rows."""

    scenario: Scenario
    tables: dict[str, list[dict]]
    reports: dict[str, list]


def _window(scenario: Scenario) -> str:
    days = [d.date for d in scenario.days]
    return f"from={min(days)}&to={max(days)}"


def _keyed(rows: list[dict], columns: tuple[str, ...]) -> dict:
    return {tuple(r[c] for c in columns): r for r in rows}


def _sums(f: oracle.Figures) -> dict:
    return {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec}


def _daily_mismatches(observed: Observed) -> list[str]:
    days = oracle.by_route_day(observed.scenario)
    key = ("route_code", "date", "service_type")
    tables = observed.tables
    return (
        _diff(
            "agg_route_daily_dist",
            {k: {**_sums(f), "on_time_count": f.on_time, "late5_count": f.over_five_min} for k, f in days.items()},
            _keyed(tables["agg_route_daily_dist"], key),
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
            _keyed(tables["agg_route_daily"], key),
        )
        + _diff(
            "agg_daily_trend",
            {k: {**_sums(f), "avg_min": _mean_min(f)} for k, f in days.items()},
            _keyed(tables["agg_daily_trend"], key),
        )
    )


def _pooled_mismatches(observed: Observed) -> list[str]:
    expected = {
        k: {
            **_sums(f),
            "avg_min": _mean_min(f),
            "on_time_pct": _pct(f.on_time_share),
            "late5_pct": _pct(f.over_five_min_share),
            "late_5min_plus": f.over_five_min,
        }
        for k, f in oracle.by_route_day_type(observed.scenario).items()
    }
    return _diff(
        "agg_route_stats", expected, _keyed(observed.tables["agg_route_stats"], ("route_code", "service_type"))
    )


def _report_mismatches(observed: Observed) -> list[str]:
    window = _window(observed.scenario)
    routes = oracle.by_route(observed.scenario)
    # on_time rows: (route_code, service, on_time_pct, avg_min, samples, ...)
    # route_table rows: (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, ...)
    on_time = {r[0]: {"on_time_pct": r[2], "avg_min": r[3], "samples": r[4]} for r in observed.reports["on_time"]}
    table = {
        r[0]: {"avg_min": r[1], "on_time_pct": r[2], "late5_pct": r[3], "late5_count": r[4], "samples": r[5]}
        for r in observed.reports["route_table"]
    }
    return _diff(
        f"GET reports/on_time {window}",
        {
            route: {"on_time_pct": _pct(f.on_time_share), "avg_min": _mean_min(f), "samples": f.departures}
            for route, f in routes.items()
        },
        on_time,
    ) + _diff(
        f"GET reports/route_table {window}",
        {
            route: {
                "avg_min": _mean_min(f),
                "on_time_pct": _pct(f.on_time_share),
                "late5_pct": _pct(f.over_five_min_share),
                "late5_count": f.over_five_min,
                "samples": f.departures,
            }
            for route, f in routes.items()
        },
        table,
    )


def _after_midnight(d: oracle.Departure) -> bool:
    return not oracle.before_midnight(d)


def _hourly_mismatches_before_midnight(observed: Observed) -> list[str]:
    """The departures scheduled before 24:00, bucket by bucket, under the
    oracle's hour and weekday. Which bucket an hour-of-day table files a
    24:10 departure under is the pipeline's to settle, so a scenario that has
    such departures may show rows the oracle does not name here."""
    scenario = observed.scenario
    before = oracle.before_midnight
    extra_ok = any(_after_midnight(d) for d in oracle.departures(scenario))
    tables = observed.tables
    return (
        _diff(
            "agg_hour_daily",
            {k: _sums(f) for k, f in oracle.by_day_hour(scenario, before).items()},
            _keyed(tables["agg_hour_daily"], ("date", "hour")),
            extra_ok=extra_ok,
        )
        + _diff(
            "agg_route_hour",
            {
                (route, day_type, gtfs_clock(sec)): _sums(f)
                for (route, day_type, sec), f in oracle.by_route_day_type_time(scenario, before).items()
            },
            _keyed(tables["agg_route_hour"], ("route_code", "service_type", "scheduled_time")),
            extra_ok=extra_ok,
        )
        + _diff(
            "agg_route_hour_dow",
            {k: _sums(f) for k, f in oracle.by_route_day_type_weekday_hour(scenario, before).items()},
            _keyed(tables["agg_route_hour_dow"], ("route_code", "service_type", "dow", "hour")),
            extra_ok=extra_ok,
        )
    )


def _after_midnight_mismatches(observed: Observed) -> list[str]:
    """The departures at 24:00 or later, wherever an hourly table files them:
    its total over all buckets less what the departures before midnight
    account for. A total, because the bucket is the pipeline's to settle."""
    scenario = observed.scenario
    by_day = {(day,): f for day, f in oracle.by_day(scenario, oracle.before_midnight).items()}
    after_by_day = {(day,): f for day, f in oracle.by_day(scenario, _after_midnight).items()}
    by_route = oracle.by_route_day_type(scenario, oracle.before_midnight)
    after_by_route = oracle.by_route_day_type(scenario, _after_midnight)
    out = []
    for table, columns, before, after in (
        ("agg_hour_daily", ("date",), by_day, after_by_day),
        ("agg_route_hour", ("route_code", "service_type"), by_route, after_by_route),
        ("agg_route_hour_dow", ("route_code", "service_type"), by_route, after_by_route),
    ):
        residue: dict = defaultdict(lambda: {"samples": 0, "sum_delay_sec": 0})
        for row in observed.tables[table]:
            key = tuple(row[c] for c in columns)
            residue[key]["samples"] += row["samples"]
            residue[key]["sum_delay_sec"] += row["sum_delay_sec"]
        for key, f in before.items():
            residue[key]["samples"] -= f.departures
            residue[key]["sum_delay_sec"] -= f.delay_sum_sec
        actual = {k: v for k, v in residue.items() if v != {"samples": 0, "sum_delay_sec": 0}}
        out += _diff(f"{table}, departures at 24:00 or later", {k: _sums(f) for k, f in after.items()}, actual)
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
    """Load, ingest, promote and analyze *scenario* for a new agency; return its id."""
    from api.main import app
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

    zip_path = tmp_dir / f"scenario_{scenario.name}.zip"
    zip_path.write_bytes(static_gtfs_zip(scenario))
    load_static(str(zip_path), agency_id, pg_conn, ch_client=ch_client)

    scenario_polls = polls(scenario)
    _closed_and_live(scenario_polls, jst_today())
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for poll in scenario_polls:
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
                raise RuntimeError(f"{poll.source_file} ({poll.stop_updates} updates): {resp.status_code} {resp.text}")

    promote_closed_days(agency_id, pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()
    return agency_id


def _read_tables(pg_conn, agency_id: int) -> dict[str, list[dict]]:
    out = {}
    with pg_conn.cursor() as cur:
        for table, columns in _TABLE_COLUMNS.items():
            cur.execute(f"SELECT {columns} FROM {table} WHERE agency_id = %s", (agency_id,))
            names = [c.name for c in cur.description]
            out[table] = [
                {n: v.isoformat() if isinstance(v, time) else v for n, v in zip(names, row, strict=True)}
                for row in cur.fetchall()
            ]
    return out


async def _read_reports(scenario: Scenario, agency_id: int) -> dict[str, list]:
    from api.main import app

    out = {}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for report, query in _REPORTS.items():
            resp = await client.get(f"/api/{agency_id}/reports/{report}?{_window(scenario)}{query}")
            if resp.status_code != 200:
                raise RuntimeError(f"reports/{report}: {resp.status_code} {resp.text}")
            out[report] = resp.json()["rows"]
    return out


def _empty_stores(pg_conn, ch_client, reset_sql: str) -> None:
    """Both stores as the conftest's per-test fixtures leave them: Postgres
    reset, ClickHouse observations and aggregates truncated, its work tables
    dropped."""
    from tests.conftest import _CH_STORE_TABLES, _drop_ch_work_tables

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(reset_sql)
    pg_conn.commit()
    for table in ("updates", "updates_live", *_CH_STORE_TABLES):
        ch_client.command(f"TRUNCATE TABLE IF EXISTS {table}")
    _drop_ch_work_tables(ch_client)


_ABSENT = object()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def observed(request, reset_sql, _ch_schema, tmp_path_factory) -> Observed:
    """One pipeline run per scenario, shared by every test of this module
    that names the scenario.

    The conftest's per-test fixtures reset both stores, so nothing the run
    wrote may outlive this fixture: it works on connections of its own,
    starts from emptied stores, reads everything the tests compare, empties
    the stores again, and hands the tests plain data. Errors in any step
    raise RuntimeError, never AssertionError, so an expected failure of a
    comparison cannot absorb a broken run.
    """
    if os.environ.get("RUN_CH_INTEGRATION") != "1":
        pytest.skip("requires `make ch-test` (RUN_CH_INTEGRATION=1)")
    from api.main import app
    from api.middleware.ratelimit import limiter
    from api.sso import SSO_ENV
    from pipeline.cache import clear_all
    from tests.conftest import _CH_TEST, _ch_test_client, _database_url, _test_pool

    scenario = request.param(_anchor_day())
    pg_conn = psycopg2.connect(_database_url())
    ch_client = _ch_test_client()
    ch_async = await clickhouse_connect.get_async_client(**{**_CH_TEST, "port": int(_CH_TEST["port"])})
    saved = {name: getattr(app.state, name, _ABSENT) for name in ("pool", "ch_client")}
    try:
        with pytest.MonkeyPatch.context() as mp:
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
            app.state.pool, app.state.ch_client = pool, ch_async
            try:
                reports = await _read_reports(scenario, agency_id)
            finally:
                await pool.close()
    finally:
        for name, value in saved.items():
            if value is _ABSENT:
                if hasattr(app.state, name):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)
        try:
            _empty_stores(pg_conn, ch_client, reset_sql)
        finally:
            pg_conn.close()
            ch_client.close()
            await ch_async.close()
    return Observed(scenario, tables, reports)


def _assert_clean(mismatches: list[str]) -> None:
    assert not mismatches, "pipeline differs from the truth:\n" + "\n".join(mismatches)


# Every test names the scenarios in this one order. pytest groups the tests
# sharing a module-scoped parameter by its position in each list, so one
# order keeps it to one pipeline run per scenario.
_SCENARIOS = [pytest.param(plain_weekday, id="plain_weekday"), pytest.param(night_route, id="night_route")]


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_daily_and_pooled_aggregates_match_the_truth(observed):
    _assert_clean(_daily_mismatches(observed) + _pooled_mismatches(observed))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_reports_match_the_truth(observed):
    _assert_clean(_report_mismatches(observed))


@pytest.mark.parametrize("observed", _SCENARIOS, indirect=True)
def test_hourly_tables_file_each_departure_before_midnight_under_its_scheduled_hour(observed):
    _assert_clean(_hourly_mismatches_before_midnight(observed))


# Ingest stores no scheduled_time for a GTFS time of 24:00:00 or later (it
# keeps scheduled_sec), and the hour-keyed tables take the hour from
# scheduled_time, so those departures fall out of every hour.
_DROPS_EXTENDED_HOURS = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="agg_hour_daily, agg_route_hour and agg_route_hour_dow leave out every departure "
    "scheduled at 24:00:00 or later, though the daily and pooled tables count it",
)


@pytest.mark.parametrize(
    "observed",
    [
        pytest.param(plain_weekday, id="plain_weekday"),
        pytest.param(night_route, id="night_route", marks=_DROPS_EXTENDED_HOURS),
    ],
    indirect=True,
)
def test_hourly_tables_hold_the_departures_after_midnight(observed):
    _assert_clean(_after_midnight_mismatches(observed))
