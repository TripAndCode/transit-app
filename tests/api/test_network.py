"""Cross-agency network summary — compute + endpoint (transit_test only)."""

import os
from datetime import date, datetime, time, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline.clickhouse import insert_updates
from pipeline.reports.network import compute_network_summary

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def net_pool(apply_schema):
    # In-process compute cache is keyed on (from_date, to_date) only, so two
    # tests sharing a date range would leak results — clear it per test.
    compute_network_summary.cache_clear()
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies, agg_route_daily_dist, agg_feed_health, updates CASCADE")
        ins = "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id"
        a = await c.fetchrow(ins, "A", "http://na")
        b = await c.fetchrow(ins, "B", "http://nb")
        cc = await c.fetchrow(ins, "C", "http://nc")
    yield pool, a["agency_id"], b["agency_id"], cc["agency_id"]
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies, agg_route_daily_dist, agg_feed_health, updates CASCADE")
    await pool.close()


async def _seed(pool, aid, *, dist, feed=None, updates_at=None):
    """dist: list of (date_iso, samples, sum_delay_sec, on_time_count). feed: (date_iso, raw, clamp)."""
    async with pool.acquire() as c:
        for d, n, sd, ot in dist:
            await c.execute(
                "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
                "samples, sum_delay_sec, on_time_count, late5_count, hist) "
                "VALUES ($1,$2,'R1','平日',$3,$4,$5,0,$6)",
                aid,
                date.fromisoformat(d),
                n,
                sd,
                ot,
                [0] * 37,
            )
        if feed:
            d, raw, clamp = feed
            await c.execute(
                "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES ($1,$2,$3,$4)",
                aid,
                date.fromisoformat(d),
                raw,
                clamp,
            )
        if updates_at:
            await c.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,'f.pb',$2,'T1','平日',$3,'R1',1,60)",
                aid,
                updates_at,
                time(11, 37),
            )


async def _seed_static_schedule(pool, aid, *, service_id: str, trip_ids: list[str], svc_date: str) -> None:
    """Seed one static-GTFS "planned trips" fixture: `len(trip_ids)` trips
    under `service_id`, all scheduled to run on `svc_date` (YYYYMMDD text,
    matching calendar_dates.txt's raw format) via exception_type=1.
    """
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1,$2,$3,1)",
            aid,
            service_id,
            svc_date,
        )
        for tid in trip_ids:
            await c.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,'R1',$3)",
                aid,
                tid,
                service_id,
            )


def _ch_row(
    trip_id: str,
    captured_at: datetime,
    *,
    stop_sequence: int = 1,
    schedule_relationship_trip: int | None = None,
    schedule_relationship_stop: int | None = None,
) -> tuple:
    """One ClickHouse `updates` row shaped for `pipeline.clickhouse.insert_updates`
    (agency_id excluded — insert_updates prepends it), with only the fields
    this feature reads populated: trip_id/stop_sequence/captured_at (dedup
    key) and schedule_relationship_trip/_stop.
    """
    return (
        "f.pb",
        captured_at,
        trip_id,
        "平日",
        "11:00:00",
        "R1",
        stop_sequence,
        60,
        None,  # stop_id
        None,  # arr_delay
        schedule_relationship_trip,
        schedule_relationship_stop,
        None,  # feed_timestamp
    )


async def test_compute_service_delivered_synthetic_cancellations(net_pool, ch_client, ch_async_client):
    """A known number of CANCELED trips produces the expected delivered-rate
    fraction, and an agency whose feed never populates schedule_relationship_
    trip reads "not available" (None) rather than a misleading 100%."""
    pool, a, b, _cc = net_pool
    svc_date_utc = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)  # 2026-04-01 11:00 JST

    # Agency A: 5 planned trips, feed populates schedule_relationship_trip;
    # 1 of the 5 (T5) is CANCELED -> 4/5 = 80.0% delivered.
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    a_rows = [_ch_row(f"T{i}", svc_date_utc, schedule_relationship_trip=0) for i in range(1, 5)]
    a_rows.append(_ch_row("T5", svc_date_utc, schedule_relationship_trip=3))
    insert_updates(ch_client, a, a_rows)

    # Agency B: 3 planned trips, but the feed never sends schedule_relationship
    # at all (every row's field is NULL, like aomori_regex's agencies) -- must
    # read as "not available", not "0 canceled / 100% delivered".
    await _seed_static_schedule(pool, b, service_id="WD", trip_ids=["U1", "U2", "U3"], svc_date="20260401")
    b_rows = [_ch_row(f"U{i}", svc_date_utc, schedule_relationship_trip=None) for i in range(1, 4)]
    insert_updates(ch_client, b, b_rows)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    by = {r["agency_id"]: r for r in rows}
    assert by[a]["planned_trips"] == 5
    assert by[a]["executed_trips"] == 4
    assert by[a]["service_delivered_pct"] == 80.0

    assert by[b]["planned_trips"] == 3
    assert by[b]["executed_trips"] is None
    assert by[b]["service_delivered_pct"] is None


async def test_compute_service_delivered_stop_level_skip_counts_as_non_executed(net_pool, ch_client, ch_async_client):
    """A trip with no trip-level CANCELED but a stop-level SKIPPED
    (schedule_relationship_stop = 1) still counts as non-executed."""
    pool, a, _b, _cc = net_pool
    svc_date_utc = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260401")
    rows = [
        _ch_row("T1", svc_date_utc, schedule_relationship_trip=0, schedule_relationship_stop=0),
        # T2 isn't trip-level CANCELED, but its one observed stop is SKIPPED.
        _ch_row("T2", svc_date_utc, schedule_relationship_trip=0, schedule_relationship_stop=1),
    ]
    insert_updates(ch_client, a, rows)

    async with pool.acquire() as conn:
        result = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in result if r["agency_id"] == a)
    assert row["planned_trips"] == 2
    assert row["executed_trips"] == 1
    assert row["service_delivered_pct"] == 50.0


async def test_compute_service_delivered_no_static_schedule_is_not_available(net_pool, ch_client, ch_async_client):
    """No static schedule loaded for the agency (planned_trips == 0) must
    also read as "not available", not divide-by-zero into a misleading 100%,
    even when the feed does populate schedule_relationship_trip."""
    pool, a, _b, _cc = net_pool
    svc_date_utc = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)
    insert_updates(ch_client, a, [_ch_row("T1", svc_date_utc, schedule_relationship_trip=0)])

    async with pool.acquire() as conn:
        result = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in result if r["agency_id"] == a)
    assert row["planned_trips"] == 0
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None


async def test_compute_rollups_ranking_and_freshness(net_pool, ch_client, ch_async_client):
    pool, a, b, cc = net_pool
    await _seed(
        pool,
        a,
        dist=[("2026-04-01", 100, 60000, 50), ("2026-04-02", 100, 60000, 50)],
        feed=("2026-04-01", 1000, 5),
        updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc),
    )
    # B's dist lags its newest completed updates day (2026-04-01 < 2026-04-02) → stale.
    await _seed(
        pool,
        b,
        dist=[("2026-04-01", 100, 12000, 100)],
        feed=("2026-04-02", 500, 50),
        updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc),
    )
    # Agency C: no data in range at all.
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, a)
    mirror_updates_to_ch(ch_client, b)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    by = {r["agency_id"]: r for r in rows}
    assert by[a]["avg_delay_min"] == 10.0
    assert by[a]["on_time_pct"] == 50.0
    assert by[a]["samples"] == 200
    assert isinstance(by[a]["avg_delay_min"], float)
    assert by[a]["clamp_pct"] == round(5 / 1000 * 100, 2)
    assert by[a]["is_stale"] is False
    assert by[a]["data_from"] == "2026-04-01"
    assert by[a]["data_to"] == "2026-04-02"
    assert by[b]["avg_delay_min"] == 2.0
    assert by[b]["on_time_pct"] == 100.0
    assert by[b]["is_stale"] is True
    assert by[cc]["avg_delay_min"] is None
    assert by[cc]["samples"] == 0
    assert by[cc]["clamp_pct"] is None
    assert by[cc]["data_from"] is None
    assert by[cc]["data_to"] is None
    order = [r["agency_id"] for r in rows]
    assert order.index(a) < order.index(b) < order.index(cc)


async def test_compute_network_summary_falls_back_to_latest_completed_day_when_today_has_rows(
    net_pool, ch_client, ch_async_client
):
    """Regression: an agency ingesting continuously (a completed day AFTER
    its agg's newest day, PLUS a row from right now) must still be flagged
    stale — is_stale must not silently flip to False just because the
    unconditional MAX(captured_at) happens to land on today (the normal,
    healthy, continuously-ingesting case in production).

    A prior version computed MAX(captured_at) over the whole table and only
    accepted it in Python if it was already before today's JST midnight —
    so it never fell back to the latest prior completed day when today also
    had rows, defeating staleness detection under normal conditions.
    """
    pool, a, _b, _cc = net_pool
    # agg only knows about 2026-04-01 ...
    await _seed(pool, a, dist=[("2026-04-01", 100, 6000, 50)])
    async with pool.acquire() as c:
        # ... but live `updates` has a LATER completed day (04-03) the agg
        # hasn't caught up to yet, plus a row from right now (still-ingesting).
        await c.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'f1.pb',$2,'T1','平日',$3,'R1',1,60)",
            a,
            datetime(2026, 4, 3, 2, 37, tzinfo=timezone.utc),
            time(11, 37),
        )
        await c.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'f_now.pb',now(),'T2','平日',$2,'R1',1,60)",
            a,
            time(11, 37),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, a)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["is_stale"] is True  # agg (04-01) lags the true latest completed day (04-03)


async def test_compute_network_summary_excludes_deleted_agency(net_pool, ch_async_client):
    pool, a, b, cc = net_pool
    async with pool.acquire() as conn:
        await conn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = $1", cc)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    ids = [r["agency_id"] for r in rows]
    assert cc not in ids
    assert a in ids
    assert b in ids


@pytest.fixture
async def net_client(net_pool, ch_async_client):
    pool, a, b, cc = net_pool
    from api.main import app

    app.state.pool = pool
    app.state.ch_client = ch_async_client
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, pool, a, b, cc


async def test_network_summary_endpoint(net_client, ch_client):
    client, pool, a, b, _cc = net_client
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)], feed=("2026-04-02", 1000, 5))
    # Agency B: dist lags its completed updates day, NO feed → clamp_pct None, stale.
    await _seed(
        pool, b, dist=[("2026-04-01", 100, 12000, 100)], updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc)
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, b)
    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "2026-04-01" and body["to"] == "2026-04-07"
    arow = next(x for x in body["agencies"] if x["agency_id"] == a)
    assert arow["avg_delay_min"] == 10.0
    assert set(arow) >= {
        "agency_id",
        "agency_name",
        "avg_delay_min",
        "on_time_pct",
        "samples",
        "raw_samples",
        "clamp_count",
        "clamp_pct",
        "is_stale",
        "planned_trips",
        "executed_trips",
        "service_delivered_pct",
    }
    brow = next(x for x in body["agencies"] if x["agency_id"] == b)
    assert brow["clamp_pct"] is None
    assert brow["is_stale"] is True


async def test_network_summary_includes_definition_metadata(net_client):
    """The board's on_time_pct always reads the exact legacy_60s column (no
    tolerance query param exists on this endpoint) -- the definition block
    must say so explicitly rather than leaving a user to assume it, and must
    match pipeline.reports.definition's resolved legacy values exactly (not
    a hardcoded/duplicated copy of them)."""
    client, _pool, _a, _b, _cc = net_client
    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    definition = body["definition"]
    assert definition["preset"] == "legacy_60s"
    assert definition["early_tolerance_sec"] is None
    assert definition["late_tolerance_sec"] == 60
    assert definition["exclusion_threshold_sec"] == 7200
    assert definition["measurement_point"] == "all_stops_all_observations"
    assert definition["dedup_rule"] == "latest_observation_per_stop_event"


async def test_network_summary_degrades_when_clickhouse_freshness_probe_fails(net_pool):
    """Fix 8a regression: ClickHouse backs ONLY the ``is_stale`` field here —
    every other field (avg_delay_min, on_time_pct, samples, raw_samples,
    clamp_pct, data_from, data_to) comes from Postgres agg_* tables. A
    ClickHouse hiccup on the per-agency freshness probe must degrade that
    agency's ``is_stale`` (via a None live_max — see is_stale's docstring:
    "no completed day / can't determine -> not stale"), not 500/503 the whole
    /api/network/summary response (mirrors api.routers.map's
    today_route_summary freshness try/except; no real ClickHouse needed — a
    client whose `.query` always raises is enough to simulate the hiccup)."""
    pool, a, _b, _cc = net_pool
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)], feed=("2026-04-02", 1000, 5))

    class _BrokenCh:
        async def query(self, *args, **kwargs):
            raise RuntimeError("simulated ClickHouse outage")

    from api.main import app

    app.state.pool = pool
    app.state.ch_client = _BrokenCh()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    arow = next(x for x in body["agencies"] if x["agency_id"] == a)
    assert arow["avg_delay_min"] == 10.0  # Postgres-backed field unaffected by the CH outage
    assert arow["is_stale"] is False  # degraded live_max=None -> is_stale(agg_day, None) is False
