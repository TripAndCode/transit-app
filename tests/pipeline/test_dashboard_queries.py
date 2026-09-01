"""Dashboard SQL aggregations (heatmap / anomalies / movers) — transit_test only.

All three cards serve from the precomputed aggregates (agg_daily_trend /
agg_route_hour). There is no live-`updates` fallback: service/time_band are
no-ops on these overview cards. Tests seed the aggregates directly.
"""

from __future__ import annotations

import os
from datetime import date, time

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


def _exact_sum_delay_sec(avg_min, samples):
    """Back-derive the exact raw-seconds sum an ``avg_min``/``samples`` pair
    would have stored, so pooling via ``SUM(sum_delay_sec)/SUM(samples)``
    reproduces the same figure these tests already assert on. A caller that
    wants the exact-vs-reweighted formulas to provably diverge must pass an
    explicit ``sum_delay_sec`` instead of relying on this default."""
    return round(float(avg_min) * 60 * int(samples))


async def _seed_trend(pool, agency_id, rows):
    """rows: (date_iso, route_code, service_type, avg_min, samples[, sum_delay_sec])."""
    expanded = []
    for d, rc, st, av, n, *rest in rows:
        sds = rest[0] if rest else _exact_sum_delay_sec(av, n)
        expanded.append((d, rc, st, av, n, sds))
    async with pool.acquire() as c:
        await c.executemany(
            "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples, "
            "sum_delay_sec) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            [(agency_id, d, rc, st, av, n, sds) for (d, rc, st, av, n, sds) in expanded],
        )
        await c.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1,$2,$3) "
            "ON CONFLICT DO NOTHING",
            [(agency_id, rc, rc) for (_d, rc, _s, _a, _n, _sds) in expanded],
        )


async def _seed_route_hour(pool, agency_id, rows):
    """rows: (route_code, service_type, scheduled_time(datetime.time), avg_min, samples[, sum_delay_sec])."""
    expanded = []
    for rc, st, sch, av, n, *rest in rows:
        sds = rest[0] if rest else _exact_sum_delay_sec(av, n)
        expanded.append((rc, st, sch, av, n, sds))
    async with pool.acquire() as c:
        await c.executemany(
            "INSERT INTO agg_route_hour (agency_id, route_code, service_type, scheduled_time, "
            "avg_min, p50_min, p90_min, samples, sum_delay_sec) VALUES ($1,$2,$3,$4,$5,$5,$5,$6,$7)",
            [(agency_id, rc, st, sch, av, n, sds) for (rc, st, sch, av, n, sds) in expanded],
        )
        await c.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1,$2,$3) "
            "ON CONFLICT DO NOTHING",
            [(agency_id, rc, rc) for (rc, *_rest) in expanded],
        )


async def test_movers_reads_agg_daily_trend(movers_pool):
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-10", "R1", "平日", 9.0, 100),
            ("2026-04-03", "R1", "平日", 3.0, 100),
            ("2026-04-10", "R2", "平日", 2.0, 100),
            ("2026-04-03", "R2", "平日", 2.0, 100),
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 8), to_date=date(2026, 4, 14))
    async with pool.acquire() as c:
        res = await movers(c, agency_id=agency_id, ctx=ctx, window_days=7, top=10)
    by = {r["route_code"]: r for r in res.rows}
    assert by["R1"]["current_avg"] == 9.0 and by["R1"]["previous_avg"] == 3.0
    assert by["R1"]["delta"] == 6.0
    assert res.rows[0]["route_code"] == "R1"
    assert by["R1"]["samples"] == 100


async def test_movers_routes_filter_fast_path(movers_pool):
    """routes filter on the agg path: only the requested route appears."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-10", "R1", "平日", 9.0, 100),
            ("2026-04-03", "R1", "平日", 3.0, 100),
            ("2026-04-10", "R2", "平日", 8.0, 100),
            ("2026-04-03", "R2", "平日", 2.0, 100),
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 8), to_date=date(2026, 4, 14), routes=("R1",))
    async with pool.acquire() as c:
        res = await movers(c, agency_id=agency_id, ctx=ctx, window_days=7, top=10)
    codes = {r["route_code"] for r in res.rows}
    assert codes == {"R1"}
    by = {r["route_code"]: r for r in res.rows}
    assert by["R1"]["current_avg"] == 9.0 and by["R1"]["previous_avg"] == 3.0


async def test_movers_returns_delta(movers_pool):
    """Row shape + ordering by abs(delta) DESC, served from agg_daily_trend."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-10", "R1", "平日", 9.0, 100),  # delta +6
            ("2026-04-03", "R1", "平日", 3.0, 100),
            ("2026-04-10", "R2", "平日", 5.0, 100),  # delta +2
            ("2026-04-03", "R2", "平日", 3.0, 100),
            ("2026-04-10", "R3", "平日", 4.0, 100),  # delta -4
            ("2026-04-03", "R3", "平日", 8.0, 100),
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 8), to_date=date(2026, 4, 14))
    async with pool.acquire() as c:
        result = await movers(c, agency_id=agency_id, ctx=ctx, window_days=7, top=10)
    assert isinstance(result, Movers)
    assert len(result.rows) == 3
    # Each row has the expected keys
    assert all(
        set(["route_code", "label", "current_avg", "previous_avg", "delta", "delta_pct", "samples"]).issubset(r.keys())
        for r in result.rows
    )
    # Ordered by abs(delta) DESC
    deltas = [abs(r["delta"]) for r in result.rows]
    assert deltas == sorted(deltas, reverse=True)
    assert result.rows[0]["route_code"] == "R1"  # |+6| is largest


async def test_anomalies_reads_agg_daily_trend(movers_pool):
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-01", "R1", "平日", 3.0, 100),
            ("2026-04-02", "R1", "平日", 3.0, 100),
            ("2026-04-03", "R1", "平日", 3.0, 100),
            ("2026-04-04", "R1", "平日", 30.0, 100),  # spike
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 4))
    async with pool.acquire() as c:
        res = await anomaly_timeline(c, agency_id=agency_id, ctx=ctx, days=30, sigma=1.5)
    assert isinstance(res, AnomalyTimeline)
    assert [s["date"] for s in res.series] == ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04"]
    assert res.series[0]["avg_delay"] == 3.0
    assert any(a["date"] == "2026-04-04" for a in res.anomalies)


async def test_heatmap_dow_from_trend(movers_pool):
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-06", "R1", "平日", 5.0, 100),  # Monday -> bucket 0
            ("2026-04-07", "R1", "平日", 8.0, 100),  # Tuesday -> bucket 1
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30))
    async with pool.acquire() as c:
        res = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="dow", top_routes=20)
    assert isinstance(res, DelayHeatmap)
    assert res.dimensions == ["月", "火", "水", "木", "金", "土", "日"]
    assert res.routes[0]["route_code"] == "R1"
    assert res.cells[0][0] == 5.0
    assert res.cells[0][1] == 8.0


async def test_heatmap_hour_band_from_route_hour(movers_pool):
    pool, agency_id = movers_pool
    await _seed_route_hour(
        pool,
        agency_id,
        [
            ("R1", "平日", time(7, 0), 6.0, 100),  # 朝 -> 0
            ("R1", "平日", time(18, 0), 12.0, 100),  # 夕 -> 2
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30))
    async with pool.acquire() as c:
        res = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="hour_band", top_routes=20)
    assert res.dimensions == ["朝", "昼", "夕", "夜"]
    assert res.cells[0][0] == 6.0
    assert res.cells[0][2] == 12.0
    assert res.cells[0][1] is None


async def test_delay_heatmap_cache_hit(movers_pool):
    """Second call with identical args returns cached result: 1 miss + 1 hit + non-empty."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-06", "R1", "平日", 5.0, 100),
            ("2026-04-07", "R1", "平日", 8.0, 100),
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30))

    perf.reset()

    async with pool.acquire() as c:
        result1 = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="dow", top_routes=20)
    async with pool.acquire() as c:
        result2 = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="dow", top_routes=20)

    snap = perf.snapshot()
    cache_stats = snap["caches"]["delay_heatmap"]
    assert cache_stats["misses"] == 1, f"expected 1 miss, got {cache_stats['misses']}"
    assert cache_stats["hits"] == 1, f"expected 1 hit, got {cache_stats['hits']}"
    assert result1 == result2
    # Non-empty result so the cache assertions aren't vacuous.
    assert result1.routes[0]["route_code"] == "R1"
    assert result1.cells[0][0] == 5.0


async def test_heatmap_dow_pools_exact_sum_delay_sec_not_reweighted_avg(movers_pool):
    """Both seeded Mondays share the same (wrong) avg_min=5.0, so the old
    SUM(avg_min*samples)/SUM(samples) reweighting would also report 5.0 --
    but sum_delay_sec backs true per-row averages of 6.0 and 2.0, so the
    exact pooled mean must be 3.0, proving the grid reads sum_delay_sec."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-06", "R1", "平日", 5.0, 100, 36000),  # Monday, true avg 6.0
            ("2026-04-13", "R1", "平日", 5.0, 300, 36000),  # Monday, true avg 2.0
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30))
    async with pool.acquire() as c:
        res = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="dow", top_routes=20)
    assert res.cells[0][0] == 3.0


async def test_heatmap_hour_band_pools_exact_sum_delay_sec_not_reweighted_avg(movers_pool):
    """Same divergence proof as the DOW grid, for the hour-band grid served
    from agg_route_hour: both rows share avg_min=5.0, but sum_delay_sec backs
    true averages of 6.0 and 2.0, so the exact pool must be 3.0."""
    pool, agency_id = movers_pool
    await _seed_route_hour(
        pool,
        agency_id,
        [
            ("R1", "平日", time(6, 0), 5.0, 100, 36000),  # 朝, true avg 6.0
            ("R1", "祝日", time(7, 0), 5.0, 300, 36000),  # 朝, true avg 2.0
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 30))
    async with pool.acquire() as c:
        res = await delay_heatmap(c, agency_id=agency_id, ctx=ctx, dimension="hour_band", top_routes=20)
    assert res.cells[0][0] == 3.0


async def test_anomalies_pools_exact_sum_delay_sec_not_reweighted_avg(movers_pool):
    """Same-date, two-service-type divergence proof for the anomaly-timeline
    series: both rows share avg_min=5.0, but sum_delay_sec backs true
    averages of 6.0 and 2.0, so the exact network-wide pool must be 3.0."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-01", "R1", "平日", 5.0, 100, 36000),  # true avg 6.0
            ("2026-04-01", "R1", "祝日", 5.0, 300, 36000),  # true avg 2.0
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 1))
    async with pool.acquire() as c:
        res = await anomaly_timeline(c, agency_id=agency_id, ctx=ctx, days=30, sigma=1.5)
    assert res.series[0]["avg_delay"] == 3.0


async def test_movers_pools_exact_sum_delay_sec_not_reweighted_avg(movers_pool):
    """Current-window divergence proof for movers: both current-window rows
    share avg_min=5.0, but sum_delay_sec backs true averages of 6.0 and 2.0,
    so the exact pooled current_avg must be 3.0, not the reweighted 5.0."""
    pool, agency_id = movers_pool
    await _seed_trend(
        pool,
        agency_id,
        [
            ("2026-04-10", "R1", "平日", 5.0, 100, 36000),  # current window, true avg 6.0
            ("2026-04-10", "R1", "祝日", 5.0, 300, 36000),  # current window, true avg 2.0
            ("2026-04-03", "R1", "平日", 1.0, 100, 6000),  # prior window, exact avg 1.0
        ],
    )
    ctx = RangeCtx(from_date=date(2026, 4, 8), to_date=date(2026, 4, 14))
    async with pool.acquire() as c:
        res = await movers(c, agency_id=agency_id, ctx=ctx, window_days=7, top=10)
    by = {r["route_code"]: r for r in res.rows}
    assert by["R1"]["current_avg"] == 3.0
