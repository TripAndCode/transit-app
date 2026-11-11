"""Every read of the observations outside analyze files each row under the
timetable in force on its service date, as analyze does
(``pipeline.live_filing``), so a live scan and the aggregates agree.

Timetable A is in force on the day read. B is published and loaded during
it, in force from the next day: it moves trip b from 12:30 to 14:10, from
the noon band to the afternoon, and drops trips c and e. The polls of b, c
and e that arrive after the load are ingested against B, so ingest stamps b
at 14:10, leaves c without a schedule, and leaves e, whose feed names no
route, without a route as well. Filed under A, b departs at 12:30 and c and
e keep their schedule and route. Trips f and g run alike in both versions.
On the closed day, trip h, the only trip of route R3, which B drops too, is
polled with no route at all, so filing alone names its route.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple

import httpx
import pytest
from httpx import ASGITransport

from api.range import RangeCtx, current_service_date
from pipeline.analyze import analyze
from pipeline.clickhouse import LIVE_TABLE
from pipeline.ingest import ingest_live_payload
from pipeline.live_filing import filing
from pipeline.promote import promote_closed_days
from pipeline.service_day import service_date_of
from pipeline.static_archive import Stamp
from pipeline.static_loader import load_static
from tests.fixtures.clock import JST
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

ROUTE, OTHER, FILED_ONLY = "R1", "R2", "R3"
STOPS = tuple(f"S{i}" for i in range(1, 7))
SERVICE = "平日"
DELAY = 60

# trip_id: (route, first departure); each trip serves STOPS two minutes apart.
_A = {
    "a": (ROUTE, "07:00"),
    "f": (ROUTE, "12:00"),
    "g": (ROUTE, "12:10"),
    "b": (ROUTE, "12:30"),
    "c": (OTHER, "12:40"),
    "e": (ROUTE, "12:48"),
    "h": (FILED_ONLY, "15:00"),
}
_B = {"a": (ROUTE, "07:00"), "f": (ROUTE, "12:00"), "g": (ROUTE, "12:10"), "b": (ROUTE, "14:10"), "d": (ROUTE, "13:00")}


def _sec(clock: str, stop: int = 0) -> int:
    hh, mm = (int(part) for part in clock.split(":"))
    return hh * 3600 + mm * 60 + 120 * stop


def _clock(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:00"


def _stem(fetched: date) -> str:
    return f"gtfs_static_{fetched:%Y%m%d}"


def _zip(tmp_path, stem: str, first: date, last: date, trips: dict[str, tuple[str, str]]) -> str:
    stop_times = [
        f"{trip_id},{i + 1},{stop},{_clock(_sec(departs, i))},{_clock(_sec(departs, i))}"
        for trip_id, (_route, departs) in trips.items()
        for i, stop in enumerate(STOPS)
    ]
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "\n".join(f"{s},{s},34.4{i},132.4{i}" for i, s in enumerate(STOPS)),
        "routes.txt": f"route_id,route_short_name\n{ROUTE},{ROUTE}\n{OTHER},{OTHER}",
        "trips.txt": "trip_id,route_id,service_id\n"
        + "\n".join(f"{t},{route},{SERVICE}" for t, (route, _departs) in trips.items()),
        "stop_times.txt": "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n" + "\n".join(stop_times),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"{SERVICE},1,1,1,1,1,1,1,{first:%Y%m%d},{last:%Y%m%d}",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    path = tmp_path / f"{stem}.zip"
    path.write_bytes(buf.getvalue())
    return str(path)


def _agency(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('live filing', 'https://live-filing.example.com/tu.pb', 'static_join') RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    return agency_id


def _poll(pg_conn, ch_client, agency_id: int, day: date, hh: int, mm: int, trip_id: str, route: str | None) -> None:
    """One poll at *hh*:*mm* JST of service day *day*, predicting every stop of *trip_id* DELAY seconds late."""
    at = datetime.combine(day, time(hh, mm), tzinfo=JST)
    feed = trip_update_feed(
        trip_update(
            trip_id,
            *(stop_time_update(i + 1, departure_delay=DELAY, stop_id=stop) for i, stop in enumerate(STOPS)),
            route_id=route,
            start_date=f"{day:%Y%m%d}",
        ),
        timestamp=int(at.timestamp()),
    )
    name = f"live_filing/{day:%Y%m%d}/TripUpdate_{trip_id}_{hh:02d}{mm:02d}00.pb"
    ingest_live_payload(agency_id, feed, at.astimezone(timezone.utc).isoformat(), name, pg_conn, ch_client)


# GTFS-RT's StopTimeUpdate SCHEDULED, SKIPPED and NO_DATA, and TripDescriptor CANCELED.
_SCHEDULED, _SKIPPED, _NO_DATA, _CANCELED = 0, 1, 2, 3


def _report(
    pg_conn,
    ch_client,
    agency_id: int,
    day: date,
    hh: int,
    mm: int,
    trip_id: str,
    stops: dict[int, tuple[int, int | None]],
    *,
    trip_status: int = _SCHEDULED,
) -> None:
    """One poll at *hh*:*mm* JST of service day *day* reporting *trip_id* with
    *trip_status*, and each stop of *stops*, by sequence, with its status and
    departure delay (None for none)."""
    at = datetime.combine(day, time(hh, mm), tzinfo=JST)
    feed = trip_update_feed(
        trip_update(
            trip_id,
            *(
                stop_time_update(seq, departure_delay=delay, stop_id=STOPS[seq - 1], schedule_relationship=status)
                for seq, (status, delay) in stops.items()
            ),
            route_id=ROUTE,
            start_date=f"{day:%Y%m%d}",
            schedule_relationship=trip_status,
        ),
        timestamp=int(at.timestamp()),
    )
    name = f"live_filing/{day:%Y%m%d}/TripUpdate_{trip_id}_{hh:02d}{mm:02d}00_status.pb"
    ingest_live_payload(agency_id, feed, at.astimezone(timezone.utc).isoformat(), name, pg_conn, ch_client)


def _load(pg_conn, ch_client, tmp_path, agency_id: int, stem: str, first: date, last: date, trips) -> None:
    load_static(_zip(tmp_path, stem, first, last, trips), agency_id, pg_conn, ch_client=ch_client)


def _poll_after_the_load(pg_conn, ch_client, agency_id: int, day: date) -> None:
    for mm, trip_id, route in ((1, "f", ROUTE), (11, "g", ROUTE), (31, "b", ROUTE), (32, "c", OTHER), (33, "e", None)):
        _poll(pg_conn, ch_client, agency_id, day, 12, mm, trip_id, route)


def _promote_and_analyze(pg_conn, ch_client, agency_id: int) -> None:
    promote_closed_days(agency_id, pg_conn, ch_client)
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()


class Refiled(NamedTuple):
    agency_id: int
    day: date  # filed under A, with polls ingested against B
    before: date  # every poll ingested against A, the version in force
    a: str
    b: str


@pytest.fixture
def refiled(pg_conn, ch_client, tmp_path) -> Refiled:
    """A closed day refiled by analyze, and the correctly stamped day before
    it. Both sit inside `updates_live`'s TTL, so every poll is still there to
    promote."""
    day = service_date_of(datetime.now(timezone.utc)) - timedelta(days=1)
    before = day - timedelta(days=1)
    agency_id = _agency(pg_conn)
    a, b = _stem(day - timedelta(days=30)), _stem(day)
    _load(pg_conn, ch_client, tmp_path, agency_id, a, day - timedelta(days=30), day + timedelta(days=150), _A)
    for service_day in (before, day):
        _poll(pg_conn, ch_client, agency_id, service_day, 7, 10, "a", ROUTE)
    _poll(pg_conn, ch_client, agency_id, before, 12, 31, "b", ROUTE)
    _promote_and_analyze(pg_conn, ch_client, agency_id)
    _load(pg_conn, ch_client, tmp_path, agency_id, b, day + timedelta(days=1), day + timedelta(days=180), _B)
    _poll(pg_conn, ch_client, agency_id, day, 12, 20, "h", None)
    _poll_after_the_load(pg_conn, ch_client, agency_id, day)
    _promote_and_analyze(pg_conn, ch_client, agency_id)
    return Refiled(agency_id, day, before, a, b)


@pytest.fixture
def withdrawn(refiled, pg_conn, ch_client) -> Refiled:
    """The refiled day, where the feed, after predicting them, reports f's last
    three stops SKIPPED, each still carrying a delay, and then cancels g. It
    reports b's last stop SKIPPED too, and then predicts it again, 90 seconds
    late. All are polled after B's load, like the predictions they follow, so
    the day reads them REFIT."""
    agency_id, day = refiled.agency_id, refiled.day
    _report(pg_conn, ch_client, agency_id, day, 12, 5, "f", {seq: (_SKIPPED, 75) for seq in (4, 5, 6)})
    every_stop = range(1, len(STOPS) + 1)
    no_data = {seq: (_NO_DATA, None) for seq in every_stop}
    _report(pg_conn, ch_client, agency_id, day, 12, 15, "g", no_data, trip_status=_CANCELED)
    _report(pg_conn, ch_client, agency_id, day, 12, 35, "b", {6: (_SKIPPED, None)})
    _report(pg_conn, ch_client, agency_id, day, 12, 37, "b", {6: (_SCHEDULED, 90)})
    _promote_and_analyze(pg_conn, ch_client, agency_id)
    return refiled


@pytest.fixture
def today(pg_conn, ch_client, tmp_path) -> tuple[int, date]:
    """The service day in progress, read live from `updates_live`, with B
    loaded during it to take effect tomorrow."""
    day = current_service_date()
    agency_id = _agency(pg_conn)
    _load(
        pg_conn,
        ch_client,
        tmp_path,
        agency_id,
        _stem(day - timedelta(days=30)),
        day - timedelta(days=30),
        day + timedelta(days=150),
        _A,
    )
    _load(pg_conn, ch_client, tmp_path, agency_id, _stem(day), day + timedelta(days=1), day + timedelta(days=180), _B)
    _poll_after_the_load(pg_conn, ch_client, agency_id, day)
    return agency_id, day


@pytest.fixture
async def client(ch_async_client):
    from api.main import app
    from api.middleware.ratelimit import limiter
    from tests.conftest import _test_pool

    saved = {name: getattr(app.state, name, None) for name in ("pool", "ch_client")}
    pool = await _test_pool()
    app.state.pool, app.state.ch_client = pool, ch_async_client
    limiter.reset()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        await pool.close()
        for name, value in saved.items():
            setattr(app.state, name, value)


class _Spy:
    """An async ClickHouse client that records each query and its parameters."""

    def __init__(self, inner):
        self._inner = inner
        self.queries: list[tuple[str, dict]] = []

    async def query(self, sql, parameters=None, **kwargs):
        self.queries.append((sql, parameters or {}))
        return await self._inner.query(sql, parameters=parameters, **kwargs)

    def scans(self) -> list[tuple[str, dict]]:
        """The dedup scans among the queries."""
        return [(sql, params) for sql, params in self.queries if "deduped AS (" in sql]


def _noon(day: date) -> RangeCtx:
    return RangeCtx(from_date=day, to_date=day, time_band="noon")


# ── the filing itself ──────────────────────────────────────────────────────


async def test_a_refiled_day_is_read_with_the_archive_and_a_correctly_stamped_day_as_ingested(
    refiled, aconn, ch_async_client
):
    on_day = await filing(aconn, ch_async_client, refiled.agency_id, refiled.day, refiled.day)
    assert on_day.stamp is Stamp.REFIT
    assert on_day.params["force_stems"] == [refiled.a]
    before = await filing(aconn, ch_async_client, refiled.agency_id, refiled.before, refiled.before)
    assert before == (Stamp.INGESTED, (refiled.before, refiled.before), {})


async def test_the_refit_decision_agrees_with_analyzes_refit_dates(refiled, aconn, ch_client, ch_async_client):
    from pipeline import static_archive
    from pipeline.ch_aggregates import days_where

    join = static_archive.schedule_join(ch_client, refiled.agency_id, None, by_service_date=True)
    for day in (refiled.before, refiled.day):
        where, params = days_where([day])
        refit = static_archive.refit_dates(ch_client, refiled.agency_id, where, params, join, (day, day))
        filed = await filing(aconn, ch_async_client, refiled.agency_id, day, day)
        assert (filed.stamp is Stamp.REFIT) == bool(refit) == (day == refiled.day)


async def test_the_live_form_of_a_refit_read_gives_the_rows_of_analyzes_form(refiled, aconn, ch_async_client):
    """The hashed archive key and the band's narrowing change the cost, never the rows."""
    from api.range import updates_filter_ch
    from pipeline.db import build_dedup_ch_sql
    from pipeline.reports.filters import _dedup_cte_ch, ctx_filing

    for band in ("noon", "morning", "afternoon"):
        ctx = RangeCtx(from_date=refiled.day, to_date=refiled.day, time_band=band)
        filed = await ctx_filing(aconn, ch_async_client, refiled.agency_id, ctx)
        assert filed.stamp is Stamp.REFIT
        cte, params = _dedup_cte_ch(ctx, filed)
        where = updates_filter_ch(ctx, refit=True)
        exact = build_dedup_ch_sql(extra_where=where.raw, filed_where=where.filed, stamp=Stamp.REFIT)
        read = "SELECT * FROM deduped ORDER BY trip_id, stop_sequence"
        live = await ch_async_client.query(f"WITH {cte} {read}", parameters={"agency_id": refiled.agency_id, **params})
        analyzes = await ch_async_client.query(
            f"WITH deduped AS ({exact}) {read}",
            parameters={"agency_id": refiled.agency_id, **where.params, **filed.params},
        )
        assert live.result_rows == analyzes.result_rows, band
        # Filed under A, only h departs in the afternoon; b departs at noon.
        assert {row[3] for row in live.result_rows if band == "afternoon"} <= {"h"}, band
        assert live.result_rows, band


async def test_a_scan_of_correctly_stamped_days_never_joins_the_schedule_archive(refiled, aconn, ch_async_client):
    from pipeline.reports.route_table import compute_route_table

    spy = _Spy(ch_async_client)
    rows = await compute_route_table(refiled.agency_id, _noon(refiled.before), aconn, ch=spy, min_samples=1)
    assert {r[0]: r[5] for r in rows} == {ROUTE: 6}
    ((sql, params),) = spy.scans()
    assert "static_schedule_archive" not in sql
    assert not [name for name in params if name.startswith(("force_", "join_"))]

    spy = _Spy(ch_async_client)
    await compute_route_table(refiled.agency_id, _noon(refiled.day), aconn, ch=spy, min_samples=1)
    ((sql, params),) = spy.scans()
    assert "static_schedule_archive" in sql
    assert params["force_stems"] == [refiled.a]


async def test_an_agency_whose_ingest_stamps_no_version_is_read_as_ingested(pg_conn, aconn, ch_async_client):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('plain', 'https://p.example.com') RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    spy = _Spy(ch_async_client)
    day = date(2026, 10, 1)
    assert await filing(aconn, spy, agency_id, day, day) == (Stamp.INGESTED, (day, day), {})
    assert spy.queries == []


# ── report scans ───────────────────────────────────────────────────────────


async def test_the_live_hourly_heatmap_matches_the_aggregate_on_a_refiled_day(refiled, aconn, ch_async_client):
    from pipeline.reports.rankings import compute_hourly_heatmap

    day = refiled.day
    from_aggregate = await compute_hourly_heatmap(refiled.agency_id, RangeCtx(from_date=day, to_date=day), aconn)
    live = await compute_hourly_heatmap(
        refiled.agency_id,
        RangeCtx(from_date=day, to_date=day, routes=(ROUTE, OTHER, FILED_ONLY)),
        aconn,
        ch=ch_async_client,
    )
    # a at 07:00; f, g, b, c and e at noon and h at 15:00, as A has them.
    assert {(c["hour"], c["samples"]) for c in from_aggregate} == {(7, 6), (12, 30), (15, 6)}
    assert live == from_aggregate


async def test_a_time_band_route_table_scan_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.route_table import compute_route_table

    rows = await compute_route_table(refiled.agency_id, _noon(refiled.day), aconn, ch=ch_async_client, min_samples=1)
    assert {r[0]: r[5] for r in rows} == {ROUTE: 24, OTHER: 6}


async def test_the_weekday_weekend_compare_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
    from pipeline.reports.rankings import _route_wd_we_avg_ch

    day = refiled.day
    spy = _Spy(ch_async_client)
    stats = await _route_wd_we_avg_ch(refiled.agency_id, _noon(day), aconn, spy, [day], [day])
    assert {route: (wd_n, we_n) for route, (_wd, wd_n, _we, we_n) in stats.items()} == {ROUTE: (24, 24)}
    # The plausibility clamp every other departure read applies.
    assert f"BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}" in spy.queries[-1][0]


async def test_the_delay_certificate_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.council import compute_delay_certificate, delay_certificate_observed

    day = refiled.day
    rows, _total = await compute_delay_certificate(
        refiled.agency_id,
        RangeCtx(from_date=day, to_date=day, routes=(ROUTE,)),
        aconn,
        ch_async_client,
        threshold_sec=0,
    )
    assert sorted(r[4] for r in rows) == ["07:00:00", "12:00:00", "12:10:00", "12:30:00", "12:48:00"]
    # c is R2's only trip: ingest left it no schedule, A gives it one at noon.
    other = RangeCtx(from_date=day, to_date=day, routes=(OTHER,), time_band="noon")
    assert await delay_certificate_observed(refiled.agency_id, other, aconn, ch_async_client)


async def test_the_delay_certificate_probe_reads_a_scope_whose_every_departure_was_withdrawn_as_observed(
    pg_conn, ch_client, aconn, ch_async_client
):
    """The feed reported on the scope, a stop SKIPPED that was never
    predicted, so the scope is observed: nothing departed, so nothing
    departed late."""
    from pipeline.clickhouse import insert_updates
    from pipeline.reports.council import compute_delay_certificate, delay_certificate_observed

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('withdrawn only', 'https://w.example.com') "
            "RETURNING agency_id"
        )
        agency_id = cur.fetchone()[0]
    pg_conn.commit()
    day = date(2026, 10, 1)
    at = datetime.combine(day, time(12), tzinfo=JST)
    # file_name, captured_at, trip_id, service_type, scheduled_time, route_code, stop_sequence, dep_delay,
    # stop_id, arr_delay, both schedule relationships (the stop SKIPPED), feed_timestamp, scheduled_sec.
    skipped = ("w.pb", at, "t", SERVICE, "12:00:00", ROUTE, 1, None, "S1", None, _SCHEDULED, _SKIPPED, None, 43200)
    insert_updates(ch_client, agency_id, [skipped])

    ctx = RangeCtx(from_date=day, to_date=day, routes=(ROUTE,))
    assert await delay_certificate_observed(agency_id, ctx, aconn, ch_async_client)
    rows, _total = await compute_delay_certificate(agency_id, ctx, aconn, ch_async_client, threshold_sec=0)
    assert rows == []


async def test_an_ask_tool_scan_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.query.tool_queries import route_dow_breakdown

    rows = await route_dow_breakdown(refiled.agency_id, _noon(refiled.day), aconn, ch_async_client, route=ROUTE)
    assert [(route, samples) for route, _dow, _avg, samples in rows] == [(ROUTE, 24)]


async def test_a_stop_visit_scan_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.filters import _stop_visits_cte_ch, ctx_filing

    ctx = RangeCtx(from_date=refiled.day, to_date=refiled.day, routes=(ROUTE,))
    cte, params = _stop_visits_cte_ch(
        ctx, await ctx_filing(aconn, ch_async_client, refiled.agency_id, ctx), include_scheduled_sec=True
    )
    result = await ch_async_client.query(
        f"WITH {cte} SELECT trip_id, scheduled_sec FROM visits WHERE stop_sequence = 1 ORDER BY trip_id",
        parameters={"agency_id": refiled.agency_id, **params},
    )
    assert result.result_rows == [
        ("a", _sec("07:00")),
        ("b", _sec("12:30")),
        ("e", _sec("12:48")),
        ("f", _sec("12:00")),
        ("g", _sec("12:10")),
    ]


async def test_the_stamp_beside_a_live_scan_names_the_version_it_was_computed_under(refiled, ch_client, client):
    from pipeline import static_archive
    from pipeline.live_filing import invalidate_schedule_joins

    window = f"from={refiled.day}&to={refiled.day}&time_band=noon&include_sparse=1"
    response = (await client.get(f"/api/{refiled.agency_id}/reports/route_table?{window}")).json()
    assert response["definition"]["source"] == "live"
    assert [(v["name"], v["days"]) for v in response["definition"]["timetable_versions"]] == [(refiled.a, 1)]
    # Each name carries its own content, as the loads and analyze runs mapped it.
    join = static_archive.schedule_join(ch_client, refiled.agency_id, None)
    content = dict(zip(join.stems, join.hashes, strict=True))
    assert content[refiled.a] != content[refiled.b]
    hashes = [v["content_hash"] for v in response["definition"]["timetable_versions"]]
    assert hashes == [content[refiled.a][:8]]

    # B's span now starts on the day read; analyze has not run since, so the
    # aggregates still name A, while a live scan files the day under B.
    static_archive.record_span(
        ch_client, refiled.agency_id, refiled.b, (refiled.day, refiled.day + timedelta(days=180))
    )
    # As a load in this process does; another process's is seen once the
    # cached archive state expires.
    invalidate_schedule_joins()
    response = (await client.get(f"/api/{refiled.agency_id}/reports/route_table?{window}")).json()
    assert [(v["name"], v["days"]) for v in response["definition"]["timetable_versions"]] == [(refiled.b, 1)]
    assert [v["content_hash"] for v in response["definition"]["timetable_versions"]] == [content[refiled.b][:8]]
    # Under B, b departs at 14:10, outside the band: f and g alone are fewer
    # departures than the table lists, so the rows computed under A are gone too.
    assert response["rows"] == []


async def test_a_departure_the_feed_withdrew_is_in_no_scan_of_a_refiled_day(withdrawn, aconn, ch_async_client):
    """Each stop's latest report decides whether it departed, in the live form
    of a REFIT read as in analyze's: of the route's 24 noon departures, f
    keeps its first three and g none, and b keeps its last stop at the delay
    predicted after the skip."""
    from api.range import updates_filter_ch
    from pipeline.db import build_dedup_ch_sql
    from pipeline.reports.filters import _dedup_cte_ch, _stop_visits_cte_ch, ctx_filing
    from pipeline.reports.rankings import _route_wd_we_avg_ch, compute_hourly_heatmap
    from pipeline.reports.route_table import compute_route_table

    agency_id, day = withdrawn.agency_id, withdrawn.day
    ctx = _noon(day)
    filed = await ctx_filing(aconn, ch_async_client, agency_id, ctx)
    assert filed.stamp is Stamp.REFIT
    cte, params = _dedup_cte_ch(ctx, filed)
    where = updates_filter_ch(ctx, refit=True)
    exact = build_dedup_ch_sql(extra_where=where.raw, filed_where=where.filed, stamp=Stamp.REFIT)
    read = "SELECT trip_id, stop_sequence, dep_delay FROM deduped ORDER BY trip_id, stop_sequence"
    live = await ch_async_client.query(f"WITH {cte} {read}", parameters={"agency_id": agency_id, **params})
    analyzes = await ch_async_client.query(
        f"WITH deduped AS ({exact}) {read}", parameters={"agency_id": agency_id, **where.params, **filed.params}
    )
    assert live.result_rows == analyzes.result_rows
    kept: dict[str, list[int]] = {}
    for trip_id, stop_sequence, _delay in live.result_rows:
        kept.setdefault(trip_id, []).append(stop_sequence)
    assert ("b", 6, 90) in live.result_rows
    every_stop = list(range(1, len(STOPS) + 1))
    assert kept == {"b": every_stop, "c": every_stop, "e": every_stop, "f": [1, 2, 3]}

    rows = await compute_route_table(agency_id, ctx, aconn, ch=ch_async_client, min_samples=1)
    assert {r[0]: r[5] for r in rows} == {ROUTE: 15, OTHER: 6}
    stats = await _route_wd_we_avg_ch(agency_id, ctx, aconn, ch_async_client, [day], [day])
    assert {route: (wd_n, we_n) for route, (_wd, wd_n, _we, we_n) in stats.items()} == {ROUTE: (15, 15)}

    whole_day = RangeCtx(from_date=day, to_date=day)
    from_aggregate = await compute_hourly_heatmap(agency_id, whole_day, aconn)
    assert {(c["hour"], c["samples"]) for c in from_aggregate} == {(7, 6), (12, 21), (15, 6)}
    scanned = RangeCtx(from_date=day, to_date=day, routes=(ROUTE, OTHER, FILED_ONLY))
    assert await compute_hourly_heatmap(agency_id, scanned, aconn, ch=ch_async_client) == from_aggregate

    route_day = RangeCtx(from_date=day, to_date=day, routes=(ROUTE,))
    cte, params = _stop_visits_cte_ch(route_day, await ctx_filing(aconn, ch_async_client, agency_id, route_day))
    visits = await ch_async_client.query(
        f"WITH {cte} SELECT trip_id, count() FROM visits GROUP BY trip_id ORDER BY trip_id",
        parameters={"agency_id": agency_id, **params},
    )
    assert visits.result_rows == [("a", 6), ("b", 6), ("e", 6), ("f", 3)]


async def test_the_map_leaves_out_a_departure_the_feed_withdrew(withdrawn, aconn, ch_async_client, client):
    from pipeline.reports.map import compute_route_shape

    agency_id, day = withdrawn.agency_id, withdrawn.day
    body = await compute_route_shape(aconn, ch_async_client, agency_id, ROUTE, _noon(day), locale="en")
    assert [s["samples"] for s in body["stops"]] == [3, 3, 3, 2, 2, 2]

    response = await client.get(f"/api/{agency_id}/today/route/{ROUTE}/trips?date={day}&time_band=noon")
    assert response.status_code == 200, response.text
    stops = {t["trip_id"]: [s["stop_sequence"] for s in t["stops"]] for t in response.json()["trips"]}
    every_stop = list(range(1, len(STOPS) + 1))
    assert stops == {"f": [1, 2, 3], "b": every_stop, "e": every_stop}

    response = await client.get(f"/api/{agency_id}/today/route/{ROUTE}/stop-profile")
    assert response.status_code == 200, response.text
    # a, b and e at every stop, and f at its first three.
    assert [s["samples"] for s in response.json()["stops"]] == [4, 4, 4, 3, 3, 3]


# ── the delay record, the playback and the map ─────────────────────────────


async def test_the_delay_record_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.delay_record import compute_record_stops, compute_record_trips

    trips = await compute_record_trips(refiled.agency_id, refiled.day, ROUTE, aconn, ch_async_client)
    assert [(t["trip_id"], t["departure_sec"]) for t in trips] == [
        ("a", _sec("07:00")),
        ("f", _sec("12:00")),
        ("g", _sec("12:10")),
        ("b", _sec("12:30")),
        ("e", _sec("12:48")),
    ]
    record = await compute_record_stops(refiled.agency_id, refiled.day, ROUTE, "b", aconn, ch_async_client)
    assert [s["scheduled_sec"] for s in record["stops"]] == [_sec("12:30", i) for i in range(len(STOPS))]


async def test_the_day_playback_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.timeline import compute_delay_timeline

    frames = await compute_delay_timeline(refiled.agency_id, refiled.day, 60, aconn, ch_async_client)
    # f, g and b at every stop in hour 12; a alone in hour 7 is too thin to draw.
    assert {f["t"]: [p["samples"] for p in f["points"]] for f in frames if f["points"]} == {"12:00": [3] * len(STOPS)}


async def test_the_route_shape_reads_the_timetable_in_force(refiled, aconn, ch_async_client):
    from pipeline.reports.map import compute_route_shape

    body = await compute_route_shape(aconn, ch_async_client, refiled.agency_id, ROUTE, _noon(refiled.day), locale="en")
    assert [s["samples"] for s in body["stops"]] == [4] * len(STOPS)


async def test_the_route_trips_read_the_timetable_in_force(refiled, client):
    response = await client.get(f"/api/{refiled.agency_id}/today/route/{ROUTE}/trips?date={refiled.day}&time_band=noon")
    assert response.status_code == 200, response.text
    first = {t["trip_id"]: t["stops"][0]["scheduled_sec"] for t in response.json()["trips"]}
    assert first == {"f": _sec("12:00"), "g": _sec("12:10"), "b": _sec("12:30"), "e": _sec("12:48")}


async def test_the_stop_profile_reads_the_timetable_in_force(refiled, client):
    response = await client.get(f"/api/{refiled.agency_id}/today/route/{ROUTE}/stop-profile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == refiled.day.isoformat()
    # a, f, g, b and e, whose route only A names.
    assert [s["samples"] for s in body["stops"]] == [5] * len(STOPS)


# ── the service day in progress ────────────────────────────────────────────


async def test_the_live_delays_read_the_timetable_in_force_today(today, client):
    agency_id, _day = today
    response = await client.get(f"/api/{agency_id}/delays/live")
    assert response.status_code == 200, response.text
    rows = {r["trip_id"]: (r["route_code"], r["scheduled_time"]) for r in response.json()["rows"]}
    assert rows == {"b": (ROUTE, "12:30:00"), "c": (OTHER, "12:40:00"), "e": (ROUTE, "12:48:00")}


async def test_a_trips_live_progress_reads_the_timetable_in_force_today(today, client):
    agency_id, _day = today
    response = await client.get(f"/api/{agency_id}/delays/live-progress?trip_id=e")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route_code"] == ROUTE
    assert [s["scheduled_time"] for s in body["stops"]] == ["12:48:00"]


async def test_todays_route_summary_reads_the_timetable_in_force(today, client):
    agency_id, _day = today
    response = await client.get(f"/api/{agency_id}/today/route-summary")
    assert response.status_code == 200, response.text
    assert {r["route_code"]: r["samples"] for r in response.json()["routes"]} == {ROUTE: 24, OTHER: 6}


async def test_todays_playback_reads_the_timetable_in_force(today, aconn, ch_async_client):
    from pipeline.reports.timeline import compute_delay_timeline

    agency_id, day = today
    frames = await compute_delay_timeline(agency_id, day, 60, aconn, ch_async_client, table=LIVE_TABLE)
    assert {f["t"]: [p["samples"] for p in f["points"]] for f in frames if f["points"]} == {"12:00": [3] * len(STOPS)}


async def test_a_route_only_filing_names_shows_its_trips_and_stop_profile(refiled, client):
    """Every poll of h came with no route; its trips and stops are R3's once filed."""
    response = await client.get(f"/api/{refiled.agency_id}/today/route/{FILED_ONLY}/trips?date={refiled.day}")
    assert response.status_code == 200, response.text
    assert [t["trip_id"] for t in response.json()["trips"]] == ["h"]
    response = await client.get(f"/api/{refiled.agency_id}/today/route/{FILED_ONLY}/stop-profile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == refiled.day.isoformat()
    assert [s["samples"] for s in body["stops"]] == [1] * len(STOPS)


# ── what a request costs ───────────────────────────────────────────────────


async def test_the_live_endpoints_probe_only_the_window_they_read(today, client, ch_async_client):
    from api.main import app
    from pipeline.live_filing import invalidate_schedule_joins

    agency_id, day = today
    latest = datetime.combine(day, time(12, 33), tzinfo=JST)
    spy = _Spy(ch_async_client)
    app.state.ch_client = spy
    for path, window in (
        ("delays/live", timedelta(minutes=5)),
        ("delays/live-progress?trip_id=e", timedelta(hours=6)),
    ):
        invalidate_schedule_joins()
        spy.queries.clear()
        response = await client.get(f"/api/{agency_id}/{path}")
        assert response.status_code == 200, response.text
        probes = [params for sql, params in spy.queries if "GROUP BY stem" in sql]
        assert [p["stamped_from"] for p in probes] == [latest - window], path


async def test_a_live_report_reads_the_archive_state_once(refiled, client, ch_async_client):
    from api.main import app
    from pipeline.live_filing import invalidate_schedule_joins

    spy = _Spy(ch_async_client)
    app.state.ch_client = spy
    day = refiled.day
    for query in (
        f"route_table?from={day}&to={day}&time_band=noon&include_sparse=1",
        # Nothing exceeds the threshold, so the certificate also asks whether the day was observed.
        f"delay_certificate?from={day}&to={day}&threshold_sec=7000",
    ):
        invalidate_schedule_joins()
        spy.queries.clear()
        response = await client.get(f"/api/{refiled.agency_id}/reports/{query}")
        assert response.status_code == 200, response.text
        assert response.json()["definition"]["source"] == "live"
        state_reads = [sql for sql, _params in spy.queries if "max(archived_at)" in sql]
        assert len(state_reads) == 1, query


async def test_a_route_seen_as_stored_finds_its_latest_day_without_deciding_how_to_file_a_month(
    refiled, client, ch_async_client
):
    """R1's polls carry their route, so the probe for its latest day answers
    from the rows as stored; only the day then read is filed."""
    from api.main import app
    from pipeline.live_filing import invalidate_schedule_joins
    from pipeline.service_day import service_day_start_utc

    spy = _Spy(ch_async_client)
    app.state.ch_client = spy
    for path in (f"today/route/{ROUTE}/trips?date={refiled.day}", f"today/route/{ROUTE}/stop-profile"):
        invalidate_schedule_joins()
        spy.queries.clear()
        response = await client.get(f"/api/{refiled.agency_id}/{path}")
        assert response.status_code == 200, response.text
        assert response.json()["date"] == refiled.day.isoformat(), path
        probed_from = [params["stamped_from"] for sql, params in spy.queries if "GROUP BY stem" in sql]
        assert probed_from, path
        assert min(probed_from) >= service_day_start_utc(refiled.day), path
