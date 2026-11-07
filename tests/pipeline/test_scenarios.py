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
declared with each day, not read from its weekday, so nothing here asserts a
table keyed on the day of the week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction

import httpx
import pytest
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


def _diff(label: str, expected: dict, actual: dict) -> list[str]:
    """Each key or cell where *actual* departs from *expected*, as one line."""
    out = []
    for key in sorted(expected.keys() | actual.keys(), key=str):
        if key not in actual:
            out.append(f"{label} {key}: expected {expected[key]}, missing")
            continue
        if key not in expected:
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


def _rows(pg_conn, sql: str, agency_id: int) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(sql, (agency_id,))
        return cur.fetchall()


def _daily_mismatches(scenario: Scenario, pg_conn, agency_id: int) -> list[str]:
    days = oracle.by_route_day(scenario)
    dist = {
        (route, day, service): {"samples": n, "sum_delay_sec": s, "on_time_count": on, "late5_count": late}
        for route, day, service, n, s, on, late in _rows(
            pg_conn,
            "SELECT route_code, date, service_type, samples, sum_delay_sec, on_time_count, late5_count "
            "FROM agg_route_daily_dist WHERE agency_id = %s",
            agency_id,
        )
    }
    daily = {
        (route, day, service): {
            "samples": n,
            "sum_delay_sec": s,
            "avg_delay_sec": avg,
            "worst_delay_sec": worst,
            "trips_observed": trips,
        }
        for route, day, service, n, s, avg, worst, trips in _rows(
            pg_conn,
            "SELECT route_code, date, service_type, samples, sum_delay_sec, avg_delay_sec, worst_delay_sec, "
            "trips_observed FROM agg_route_daily WHERE agency_id = %s",
            agency_id,
        )
    }
    trend = {
        (route, day, service): {"samples": n, "sum_delay_sec": s, "avg_min": avg}
        for route, day, service, n, s, avg in _rows(
            pg_conn,
            "SELECT route_code, date, service_type, samples, sum_delay_sec, avg_min "
            "FROM agg_daily_trend WHERE agency_id = %s",
            agency_id,
        )
    }
    return (
        _diff(
            "agg_route_daily_dist",
            {
                k: {
                    "samples": f.departures,
                    "sum_delay_sec": f.delay_sum_sec,
                    "on_time_count": f.on_time,
                    "late5_count": f.over_five_min,
                }
                for k, f in days.items()
            },
            dist,
        )
        + _diff(
            "agg_route_daily",
            {
                k: {
                    "samples": f.departures,
                    "sum_delay_sec": f.delay_sum_sec,
                    "avg_delay_sec": Rounded(f.mean_delay_sec, 0),
                    "worst_delay_sec": f.worst_delay_sec,
                    "trips_observed": f.trip_runs,
                }
                for k, f in days.items()
            },
            daily,
        )
        + _diff(
            "agg_daily_trend",
            {
                k: {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec, "avg_min": _mean_min(f)}
                for k, f in days.items()
            },
            trend,
        )
    )


def _pooled_mismatches(scenario: Scenario, pg_conn, agency_id: int) -> list[str]:
    stats = {
        (route, service): {
            "samples": n,
            "sum_delay_sec": s,
            "avg_min": avg,
            "on_time_pct": on_time,
            "late5_pct": late5,
            "late_5min_plus": late,
        }
        for route, service, n, s, avg, on_time, late5, late in _rows(
            pg_conn,
            "SELECT route_code, service_type, samples, sum_delay_sec, avg_min, on_time_pct, late5_pct, "
            "late_5min_plus FROM agg_route_stats WHERE agency_id = %s",
            agency_id,
        )
    }
    expected = {
        k: {
            "samples": f.departures,
            "sum_delay_sec": f.delay_sum_sec,
            "avg_min": _mean_min(f),
            "on_time_pct": _pct(f.on_time_share),
            "late5_pct": _pct(f.over_five_min_share),
            "late_5min_plus": f.over_five_min,
        }
        for k, f in oracle.by_route_day_type(scenario).items()
    }
    return _diff("agg_route_stats", expected, stats)


async def _report_mismatches(scenario: Scenario, client, agency_id: int) -> list[str]:
    first, last = min(d.date for d in scenario.days), max(d.date for d in scenario.days)
    window = f"from={first}&to={last}"
    routes = oracle.by_route(scenario)
    on_time = await client.get(f"/api/{agency_id}/reports/on_time?{window}")
    table = await client.get(f"/api/{agency_id}/reports/route_table?{window}&include_sparse=1")
    assert on_time.status_code == 200, on_time.text
    assert table.status_code == 200, table.text
    # on_time rows: (route_code, service, on_time_pct, avg_min, samples, ...)
    # route_table rows: (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, ...)
    on_time_rows = {r[0]: {"on_time_pct": r[2], "avg_min": r[3], "samples": r[4]} for r in on_time.json()["rows"]}
    table_rows = {
        r[0]: {"avg_min": r[1], "on_time_pct": r[2], "late5_pct": r[3], "late5_count": r[4], "samples": r[5]}
        for r in table.json()["rows"]
    }
    return _diff(
        f"GET reports/on_time {window}",
        {
            route: {"on_time_pct": _pct(f.on_time_share), "avg_min": _mean_min(f), "samples": f.departures}
            for route, f in routes.items()
        },
        on_time_rows,
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
        table_rows,
    )


def _hourly_mismatches(scenario: Scenario, pg_conn, agency_id: int) -> list[str]:
    """Whatever hour a breakdown by hour of day files a departure under, every
    departure has to be in some hour of it. Summed over the hours, so the
    check holds however a 24:10 departure is bucketed and whatever weekday
    the anchor date falls on."""
    by_date = {
        day: {"samples": n, "sum_delay_sec": s}
        for day, n, s in _rows(
            pg_conn,
            "SELECT date, sum(samples), sum(sum_delay_sec) FROM agg_hour_daily WHERE agency_id = %s GROUP BY date",
            agency_id,
        )
    }
    pooled = oracle.by_route_day_type(scenario)
    out = _diff(
        "agg_hour_daily summed over hours",
        {d: {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec} for d, f in oracle.by_day(scenario).items()},
        by_date,
    )
    for table, over in (("agg_route_hour", "scheduled times"), ("agg_route_hour_dow", "weekdays and hours")):
        by_route = {
            (route, service): {"samples": n, "sum_delay_sec": s}
            for route, service, n, s in _rows(
                pg_conn,
                f"SELECT route_code, service_type, sum(samples), sum(sum_delay_sec) FROM {table} "
                "WHERE agency_id = %s GROUP BY route_code, service_type",
                agency_id,
            )
        }
        out += _diff(
            f"{table} summed over {over}",
            {k: {"samples": f.departures, "sum_delay_sec": f.delay_sum_sec} for k, f in pooled.items()},
            by_route,
        )
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


async def _run_scenario(scenario: Scenario, pg_conn, ch_client, tmp_path, monkeypatch) -> int:
    """Load, ingest, promote and analyze *scenario* for a new agency; return its id.

    A step that fails raises RuntimeError, not AssertionError, so an expected
    failure of a comparison never absorbs a broken setup."""
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

    zip_path = tmp_path / f"scenario_{scenario.name}.zip"
    zip_path.write_bytes(static_gtfs_zip(scenario))
    load_static(str(zip_path), agency_id, pg_conn, ch_client=ch_client)

    scenario_polls = polls(scenario)
    _closed_and_live(scenario_polls, jst_today())
    monkeypatch.setenv("COLLECTOR_INGEST_SECRET", _COLLECTOR_SECRET)
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


@pytest.fixture
async def report_client(ch_async_client):
    from api.main import app
    from api.middleware.ratelimit import limiter
    from tests.conftest import _test_pool

    limiter.reset()
    pool = await _test_pool()
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        await pool.close()


_SCENARIOS = [pytest.param(plain_weekday, id="plain_weekday"), pytest.param(night_route, id="night_route")]


@pytest.mark.parametrize("build", _SCENARIOS)
async def test_aggregates_and_reports_match_the_truth(build, pg_conn, ch_client, report_client, tmp_path, monkeypatch):
    scenario = build(_anchor_day())
    agency_id = await _run_scenario(scenario, pg_conn, ch_client, tmp_path, monkeypatch)

    mismatches = (
        _daily_mismatches(scenario, pg_conn, agency_id)
        + _pooled_mismatches(scenario, pg_conn, agency_id)
        + await _report_mismatches(scenario, report_client, agency_id)
    )
    assert not mismatches, "pipeline differs from the truth:\n" + "\n".join(mismatches)


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
    "build",
    [
        pytest.param(plain_weekday, id="plain_weekday"),
        pytest.param(night_route, id="night_route", marks=_DROPS_EXTENDED_HOURS),
    ],
)
async def test_hourly_breakdowns_keep_every_departure(build, pg_conn, ch_client, tmp_path, monkeypatch):
    scenario = build(_anchor_day())
    agency_id = await _run_scenario(scenario, pg_conn, ch_client, tmp_path, monkeypatch)

    mismatches = _hourly_mismatches(scenario, pg_conn, agency_id)
    assert not mismatches, "pipeline differs from the truth:\n" + "\n".join(mismatches)
