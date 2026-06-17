"""Dashboard SQL aggregations (heatmap / anomalies / movers) — transit_test only."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline import perf
from pipeline.dashboard_queries import (
    AnomalyTimeline,
    DelayHeatmap,
    Movers,
    anomaly_timeline,
    delay_heatmap,
    movers,
)

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_seed(apply_schema):
    """Pool + agency + a small seed of static_routes + updates rows."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute(
            "DELETE FROM updates WHERE agency_id IN (SELECT agency_id FROM agencies WHERE feed_url = 'http://t')"
        )
        await c.execute(
            "DELETE FROM static_routes WHERE agency_id IN (SELECT agency_id FROM agencies WHERE feed_url = 'http://t')"
        )
        await c.execute("DELETE FROM agencies WHERE feed_url = 'http://t'")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        # Seed a couple of routes
        await c.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES "
            "($1, 'R1', 'R1'), ($1, 'R2', 'R2'), ($1, 'R3', 'R3')",
            agency_id,
        )
        # Seed updates rows: vary route + day-of-week + dep_delay.
        # updates schema: agency_id, file_name, captured_at (timestamptz),
        #                 trip_id, service_type, scheduled_time (TIME),
        #                 route_code, stop_sequence, dep_delay
        today = date(2026, 5, 31)
        seed_rows = []
        for i in range(28):
            d = today - timedelta(days=i)
            wd = d.weekday()
            r1_delay = 60 if wd < 5 else 180
            r2_delay = 30
            r3_delay = 0 if i != 5 else 600  # anomaly day
            for h in (8, 17):
                svc = "平日" if wd < 5 else "土日祁"  # 平日 / 土日祝
                cap_at = datetime(d.year, d.month, d.day, h, 0, 0, tzinfo=timezone.utc)
                seed_rows.append(("R1", svc, d, h, r1_delay, cap_at))
                seed_rows.append(("R2", svc, d, h, r2_delay, cap_at))
                seed_rows.append(("R3", svc, d, h, r3_delay, cap_at))
        for idx, (route, service, d, h, delay, cap_at) in enumerate(seed_rows):
            await c.execute(
                """INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type,
                       scheduled_time, route_code, stop_sequence, dep_delay)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, 1, $8)""",
                agency_id,
                f"seed_{idx}",
                cap_at,
                f"T_{route}_{d.isoformat()}_{h}",
                service,
                time(h, 0),
                route,
                delay,
            )
        yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM updates WHERE agency_id = $1", agency_id)
        await c.execute("DELETE FROM static_routes WHERE agency_id = $1", agency_id)
        await c.execute("DELETE FROM agencies WHERE agency_id = $1", agency_id)
    await pool.close()


@pytest.fixture
async def movers_pool(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM agencies WHERE feed_url = 'http://dash-agg'")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('DashAgg','http://dash-agg') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies, agg_daily_trend, agg_route_hour, agg_route_stats, static_routes CASCADE")
    await pool.close()


async def _seed_trend(pool, agency_id, rows):
    """rows: list of (date_iso, route_code, service_type, avg_min, samples)."""
    async with pool.acquire() as c:
        await c.executemany(
            "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            [(agency_id, d, rc, st, av, n) for (d, rc, st, av, n) in rows],
        )
        await c.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1,$2,$3) "
            "ON CONFLICT DO NOTHING",
            [(agency_id, rc, rc) for (_d, rc, _s, _a, _n) in rows],
        )


async def test_movers_reads_agg_daily_trend(movers_pool):
    pool, agency_id = movers_pool
    await _seed_trend(pool, agency_id, [
        ("2026-04-10", "R1", "平日", 9.0, 100),
        ("2026-04-03", "R1", "平日", 3.0, 100),
        ("2026-04-10", "R2", "平日", 2.0, 100),
        ("2026-04-03", "R2", "平日", 2.0, 100),
    ])
    ctx = RangeCtx(from_date=date(2026, 4, 8), to_date=date(2026, 4, 14))
    async with pool.acquire() as c:
        res = await movers(c, agency_id=agency_id, ctx=ctx, window_days=7, top=10)
    by = {r["route_code"]: r for r in res.rows}
    assert by["R1"]["current_avg"] == 9.0 and by["R1"]["previous_avg"] == 3.0
    assert by["R1"]["delta"] == 6.0
    assert res.rows[0]["route_code"] == "R1"
    assert by["R1"]["samples"] == 100


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 31))


@pytest.mark.asyncio
async def test_delay_heatmap_by_dow_shape(conn_with_seed):
    pool, agency = conn_with_seed
    async with pool.acquire() as c:
        result = await delay_heatmap(c, agency_id=agency, ctx=_ctx(), dimension="dow", top_routes=20)
    assert isinstance(result, DelayHeatmap)
    # Routes ordered by sample count; we seeded 3 routes so all should appear
    assert len(result.routes) == 3
    # 7 DOW buckets
    assert len(result.dimensions) == 7
    # cells: routes × dimensions, may have None for missing slots
    assert len(result.cells) == 3
    assert all(len(row) == 7 for row in result.cells)


@pytest.mark.asyncio
async def test_delay_heatmap_by_hour_band(conn_with_seed):
    """hour_band dimension buckets captured_at by hour-of-day band."""
    pool, agency = conn_with_seed
    async with pool.acquire() as c:
        result = await delay_heatmap(c, agency_id=agency, ctx=_ctx(), dimension="hour_band", top_routes=20)
    # Hour bands: morning (5-9), midday (10-15), evening (16-20), night (21-4)
    assert len(result.dimensions) == 4


@pytest.mark.asyncio
async def test_anomaly_timeline_detects_spike(conn_with_seed):
    pool, agency = conn_with_seed
    async with pool.acquire() as c:
        result = await anomaly_timeline(c, agency_id=agency, ctx=_ctx(), days=30, sigma=2.0)
    assert isinstance(result, AnomalyTimeline)
    # 28 days seeded, expect a series entry per day with avg_delay
    assert len(result.series) >= 28
    # One anomaly day (the R3 600s spike on i=5) — but the daily average across R1/R2/R3
    # will spike noticeably; mean+2σ should flag it.
    assert len(result.anomalies) >= 1
    # Each anomaly carries a date + delta_sigma
    for a in result.anomalies:
        assert "date" in a and "delta_sigma" in a
        assert a["delta_sigma"] >= 2.0


@pytest.mark.asyncio
async def test_movers_returns_delta(conn_with_seed):
    pool, agency = conn_with_seed
    async with pool.acquire() as c:
        result = await movers(c, agency_id=agency, ctx=_ctx(), window_days=7, top=10)
    assert isinstance(result, Movers)
    # Each row has route_code, label, current_avg, previous_avg, delta, delta_pct, samples
    assert all(
        set(["route_code", "label", "current_avg", "previous_avg", "delta", "delta_pct", "samples"]).issubset(r.keys())
        for r in result.rows
    )
    # Ordered by abs(delta) DESC
    deltas = [abs(r["delta"]) for r in result.rows]
    assert deltas == sorted(deltas, reverse=True)


@pytest.mark.asyncio
async def test_delay_heatmap_cache_hit(conn_with_seed):
    """Second call with identical args returns cached result: 1 miss + 1 hit."""
    pool, agency = conn_with_seed
    ctx = _ctx()

    perf.reset()

    async with pool.acquire() as c:
        result1 = await delay_heatmap(c, agency_id=agency, ctx=ctx, dimension="dow", top_routes=20)
    async with pool.acquire() as c:
        result2 = await delay_heatmap(c, agency_id=agency, ctx=ctx, dimension="dow", top_routes=20)

    snap = perf.snapshot()
    cache_stats = snap["caches"]["delay_heatmap"]
    assert cache_stats["misses"] == 1, f"expected 1 miss, got {cache_stats['misses']}"
    assert cache_stats["hits"] == 1, f"expected 1 hit, got {cache_stats['hits']}"
    assert result1 == result2
