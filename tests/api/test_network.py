"""Cross-agency network summary — compute + endpoint (transit_test only)."""

import os
from datetime import date, datetime, time, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

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
    }
    brow = next(x for x in body["agencies"] if x["agency_id"] == b)
    assert brow["clamp_pct"] is None
    assert brow["is_stale"] is True
