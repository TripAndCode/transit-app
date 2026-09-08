"""Cross-agency network summary — compute + endpoint (transit_test only)."""

import os
from datetime import date, datetime, time, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline.reports.network import compute_network_summary

DATABASE_URL = os.environ["DATABASE_URL"]


_TRUNCATE_SQL = (
    "TRUNCATE agencies, agg_route_daily_dist, agg_feed_health, agg_service_delivered_daily, "
    "ridership_weights, updates CASCADE"
)


@pytest.fixture
async def net_pool(apply_schema):
    # In-process compute cache is keyed on (from_date, to_date) only, so two
    # tests sharing a date range would leak results — clear it per test.
    compute_network_summary.cache_clear()
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
        ins = "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id"
        a = await c.fetchrow(ins, "A", "http://na")
        b = await c.fetchrow(ins, "B", "http://nb")
        cc = await c.fetchrow(ins, "C", "http://nc")
    yield pool, a["agency_id"], b["agency_id"], cc["agency_id"]
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
    await pool.close()


async def _seed_route_dist(pool, aid, route_code, dist):
    """dist: list of (date_iso, samples, sum_delay_sec, on_time_count) for one route_code
    (unlike _seed below, which hardcodes route_code='R1' -- this lets a test seed
    several distinct routes under one agency, needed for ridership-weighting tests)."""
    async with pool.acquire() as c:
        for d, n, sd, ot in dist:
            await c.execute(
                "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
                "samples, sum_delay_sec, on_time_count, late5_count, hist) "
                "VALUES ($1,$2,$3,'平日',$4,$5,$6,0,$7)",
                aid,
                date.fromisoformat(d),
                route_code,
                n,
                sd,
                ot,
                [0] * 37,
            )


async def _seed_ridership_weight(pool, aid, route_code, weight):
    """route_code=None inserts that agency's default (route_code IS NULL) weight row."""
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ridership_weights (agency_id, route_code, weight) VALUES ($1,$2,$3)",
            aid,
            route_code,
            weight,
        )


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


async def _set_ingest_strategy(pool, aid, strategy):
    async with pool.acquire() as c:
        await c.execute("UPDATE agencies SET ingest_strategy = $1 WHERE agency_id = $2", strategy, aid)


async def _seed_static_version_summary(pool, aid, version, trip_count, vehicle_km=None, computed_at=None):
    """Seed one `agg_static_version_summary` row directly (bypassing
    `pipeline.analyze.analyze()`, same "seed the precomputed aggregate
    directly" convention as `_seed_service_delivered_daily` below) --
    `computed_at` lets a test control which of several rows for one agency
    is "most recently computed" (the read path's tie-break)."""
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO agg_static_version_summary "
            "(agency_id, static_version_id, trip_count, vehicle_km, computed_at) "
            "VALUES ($1,$2,$3,$4,COALESCE($5, now()))",
            aid,
            version,
            trip_count,
            vehicle_km,
            computed_at,
        )


async def _seed_service_delivered_daily(pool, aid, rows):
    """rows: list of (date_iso, non_executed_trips)."""
    async with pool.acquire() as c:
        for d, n in rows:
            await c.execute(
                "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES ($1,$2,$3)",
                aid,
                date.fromisoformat(d),
                n,
            )


async def test_compute_service_delivered_reads_precomputed_daily_aggregate(net_pool, ch_async_client):
    """The read path sums agg_service_delivered_daily over the range and
    divides against the static schedule's planned count -- no ClickHouse
    access. 5 planned trips, 1 non-executed trip-day precomputed -> 80%."""
    pool, a, b, _cc = net_pool
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    # Agency B: static_join too, but its feed had zero cancellations in range
    # (no agg_service_delivered_daily row at all) -> reads 100%, not "not available".
    await _seed_static_schedule(pool, b, service_id="WD", trip_ids=["U1", "U2", "U3"], svc_date="20260401")
    await _set_ingest_strategy(pool, b, "static_join")

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    by = {r["agency_id"]: r for r in rows}
    assert by[a]["planned_trips"] == 5
    assert by[a]["executed_trips"] == 4
    assert by[a]["service_delivered_pct"] == 80.0
    assert by[b]["planned_trips"] == 3
    assert by[b]["executed_trips"] == 3
    assert by[b]["service_delivered_pct"] == 100.0


async def test_compute_service_delivered_not_available_when_not_static_join(net_pool, ch_async_client):
    """An agency whose ingest_strategy isn't static_join reads "not available"
    (None), never a misleading 100%, regardless of static-schedule data."""
    pool, _a, b, _cc = net_pool
    await _seed_static_schedule(pool, b, service_id="WD", trip_ids=["U1", "U2", "U3"], svc_date="20260401")
    # b's ingest_strategy is left NULL (net_pool's INSERT never sets it).

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == b)
    assert row["planned_trips"] == 3
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None


async def test_compute_service_delivered_no_static_schedule_is_not_available(net_pool, ch_async_client):
    """No static schedule loaded (planned_trips == 0) reads "not available",
    never a divide-by-zero 100%, even for a static_join agency with
    precomputed non-executed rows."""
    pool, a, _b, _cc = net_pool
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 0
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None


async def test_compute_service_delivered_clamps_non_executed_exceeding_planned(net_pool, ch_async_client):
    """A non_executed_trips total exceeding planned_trips (feed drift, or a
    RT-only ADDED trip marked CANCELED) must clamp executed_trips at 0, never
    go negative."""
    pool, a, _b, _cc = net_pool
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 5)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 2
    assert row["executed_trips"] == 0
    assert row["service_delivered_pct"] == 0.0


async def test_compute_supply_metrics_vehicle_km_delivered_uses_service_delivered_ratio(net_pool, ch_async_client):
    """Item 98: vehicle_km_delivered_pct is item 92's executed/planned trip
    ratio applied to the current static-version's planned vehicle-km, once
    BOTH are available."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(pool, a, "v1", trip_count=10, vehicle_km=100.0)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])  # 4/5 executed -> 80%

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] == "v1"
    assert row["planned_trip_count"] == 10
    assert row["planned_vehicle_km"] == 100.0
    assert row["vehicle_km_delivered_pct"] == 80.0


async def test_compute_supply_metrics_falls_back_to_trip_count_only_without_shapes(net_pool, ch_async_client):
    """No shapes.txt loaded for this static version (vehicle_km NULL) ->
    vehicle_km_delivered_pct reads None (trip-count-only fallback) even
    though item 92's ratio IS available -- planned_trip_count alone still
    carries the headline."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(pool, a, "v1", trip_count=10, vehicle_km=None)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trip_count"] == 10
    assert row["planned_vehicle_km"] is None
    assert row["vehicle_km_delivered_pct"] is None


async def test_compute_supply_metrics_not_available_without_any_recorded_version(net_pool, ch_async_client):
    """No agg_static_version_summary row at all for this agency -> every
    supply field reads None, never a misleading 0/100%."""
    pool, a, _b, _cc = net_pool

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] is None
    assert row["planned_trip_count"] is None
    assert row["planned_vehicle_km"] is None
    assert row["vehicle_km_delivered_pct"] is None


async def test_compute_supply_metrics_reads_most_recently_computed_version(net_pool, ch_async_client):
    """Two versions recorded for one agency (a past reload's row persists
    alongside the current one -- see pipeline.reports.supply) -> the network
    summary headline reads the MOST RECENTLY COMPUTED one, not the
    alphabetically- or numerically-first static_version_id."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(
        pool, a, "v1_old", trip_count=5, vehicle_km=50.0, computed_at=datetime(2026, 3, 1, tzinfo=timezone.utc)
    )
    await _seed_static_version_summary(
        pool, a, "v2_new", trip_count=8, vehicle_km=80.0, computed_at=datetime(2026, 4, 1, tzinfo=timezone.utc)
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] == "v2_new"
    assert row["planned_trip_count"] == 8
    assert row["planned_vehicle_km"] == 80.0


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
        "has_ridership_weights",
        "weighted_on_time_pct",
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


async def test_ridership_weighted_on_time_absent_without_a_weight_table(net_pool, ch_async_client):
    """An agency with zero ridership_weights rows has no weighting configured
    at all -- has_ridership_weights must read False and weighted_on_time_pct
    None, never a value silently computed with an implicit weight of 1
    everywhere."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 50)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["has_ridership_weights"] is False
    assert row["weighted_on_time_pct"] is None


async def test_ridership_weighted_on_time_shifts_toward_the_heavily_weighted_route(net_pool, ch_async_client):
    """R1 is high-ridership (weight 10x) and on-time 90% of the time; R2 is
    low-ridership (weight 1x, the un-weighted default) and on-time only 10%
    of the time -- same sample count each, so the UNWEIGHTED rate sits at the
    midpoint (50%). The ridership-weighted rate must sit closer to R1's own
    90% than the unweighted rate does, i.e. shift toward the low-ridership
    route's poor performance LESS than the unweighted rate would."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, "R2", 1)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["on_time_pct"] == 50.0
    assert row["has_ridership_weights"] is True
    # (90*10 + 10*1) / (100*10 + 100*1) * 100 = 910/1100*100 = 82.7
    assert row["weighted_on_time_pct"] == 82.7
    assert abs(row["weighted_on_time_pct"] - 90) < abs(row["on_time_pct"] - 90)


async def test_ridership_weighted_on_time_uses_agency_default_weight_as_fallback(net_pool, ch_async_client):
    """A route with no route-specific ridership_weights row falls back to its
    agency's default (route_code IS NULL) row, not an implicit 1, when one is
    configured."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, None, 5)  # agency default, covers R2

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    # (90*10 + 10*5) / (100*10 + 100*5) * 100 = 950/1500*100 = 63.3
    assert row["weighted_on_time_pct"] == 63.3


async def test_ridership_weighted_on_time_none_when_configured_but_no_samples_in_range(net_pool, ch_async_client):
    """Configured (has_ridership_weights True) but zero agg_route_daily_dist
    samples in the requested range reads None, distinguishable from "not
    configured" only via has_ridership_weights, not by weighted_on_time_pct
    alone."""
    pool, a, _b, _cc = net_pool
    await _seed_ridership_weight(pool, a, "R1", 10)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["has_ridership_weights"] is True
    assert row["weighted_on_time_pct"] is None


async def test_network_summary_endpoint_surfaces_ridership_weighting(net_client):
    """End-to-end: the same weighting behavior is reachable through
    GET /api/network/summary, not just the compute layer."""
    client, pool, a, _b, _cc = net_client
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, "R2", 1)

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-01"})
    assert r.status_code == 200
    arow = next(x for x in r.json()["agencies"] if x["agency_id"] == a)
    assert arow["has_ridership_weights"] is True
    assert arow["weighted_on_time_pct"] == 82.7
