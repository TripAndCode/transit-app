"""DB-backed tests for pipeline.health.aggregate_freshness (the async ops-health
checks). Guards the _LIVE_MAX_SQL rewrite (LATERAL per agency instead of a bare
GROUP BY over all of `updates`) against a regression in the computed values."""

import os
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from pipeline.health import aggregate_freshness

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")
JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
async def health_pool(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies, updates, agg_route_daily, agg_meta CASCADE")
    yield pool
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies, updates, agg_route_daily, agg_meta CASCADE")
    await pool.close()


async def _insert_update(conn, agency_id, day, seq=1):
    captured_at = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), time(11, 37), tzinfo=JST).astimezone(
        timezone.utc
    )
    await conn.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1, $2, $3, $4, '平日', $5, 'R1', $6, 60)",
        agency_id,
        f"f{day}_{seq}.pb",
        captured_at,
        f"trip_{day}_{seq}",
        time(11, 37),
        seq,
    )


@pytest.mark.asyncio
async def test_aggregate_freshness_data_to_matches_latest_completed_day(health_pool):
    async with health_pool.acquire() as conn:
        a = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('A', 'http://a') RETURNING agency_id"
        )
        aid = a["agency_id"]
        await _insert_update(conn, aid, "2026-04-01")
        await _insert_update(conn, aid, "2026-04-03")  # latest completed day

        result = await aggregate_freshness(conn)

    row = next(r for r in result if r.agency_id == aid)
    assert row.data_to == "2026-04-03"


@pytest.mark.asyncio
async def test_aggregate_freshness_no_updates_gives_null_data_to(health_pool):
    async with health_pool.acquire() as conn:
        a = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('NoData', 'http://nodata') RETURNING agency_id"
        )
        aid = a["agency_id"]

        result = await aggregate_freshness(conn)

    row = next(r for r in result if r.agency_id == aid)
    assert row.data_to is None
    assert row.is_stale is False  # is_stale(None, None) — nothing to be behind on


@pytest.mark.asyncio
async def test_aggregate_freshness_excludes_deleted_agency(health_pool):
    async with health_pool.acquire() as conn:
        active = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Active', 'http://active') RETURNING agency_id"
        )
        deleted = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES ('Deleted', 'http://deleted', now()) "
            "RETURNING agency_id"
        )
        await _insert_update(conn, deleted["agency_id"], "2026-04-01")

        result = await aggregate_freshness(conn)

    ids = [r.agency_id for r in result]
    assert active["agency_id"] in ids
    assert deleted["agency_id"] not in ids
