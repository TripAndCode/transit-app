"""DB-backed tests for pipeline.health.aggregate_freshness (the async ops-health
checks). Guards the _LIVE_MAX_SQL rewrite (LATERAL per agency instead of a bare
GROUP BY over all of `updates`) against a regression in the computed values."""

import os
from datetime import datetime, time, timedelta, timezone
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
async def test_aggregate_freshness_data_to_matches_latest_completed_day(health_pool, ch_client, ch_async_client):
    async with health_pool.acquire() as conn:
        a = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('A', 'http://a') RETURNING agency_id"
        )
        aid = a["agency_id"]
        await _insert_update(conn, aid, "2026-04-01")
        await _insert_update(conn, aid, "2026-04-03")  # latest completed day

        from tests.conftest import mirror_updates_to_ch

        mirror_updates_to_ch(ch_client, aid)

        result = await aggregate_freshness(conn, ch_async_client)

    row = next(r for r in result if r.agency_id == aid)
    assert row.data_to == "2026-04-03"


@pytest.mark.asyncio
async def test_aggregate_freshness_no_updates_gives_null_data_to(health_pool, ch_async_client):
    async with health_pool.acquire() as conn:
        a = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('NoData', 'http://nodata') RETURNING agency_id"
        )
        aid = a["agency_id"]

        result = await aggregate_freshness(conn, ch_async_client)

    row = next(r for r in result if r.agency_id == aid)
    assert row.data_to is None
    assert row.is_stale is False  # is_stale(None, None) — nothing to be behind on


@pytest.mark.asyncio
async def test_aggregate_freshness_falls_back_to_latest_completed_day_when_today_has_rows(
    health_pool, ch_client, ch_async_client
):
    """Regression: an agency ingesting continuously (rows from a completed
    past day AND from right now, no agg_route_daily seeded) must report the
    latest COMPLETED day as data_to and be flagged stale — not silently
    "heal" to data_to=None/is_stale=False just because the unconditional
    MAX(captured_at) happens to land on today (the normal, healthy,
    continuously-ingesting case in production).

    A prior version of aggregate_freshness computed MAX(captured_at) over
    the whole table and only accepted it in Python if it was already before
    today's JST midnight — which meant it NEVER fell back to the latest
    prior completed day when today also had rows, defeating staleness
    detection under totally normal operating conditions.
    """
    async with health_pool.acquire() as conn:
        a = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Ingesting', 'http://ingesting') RETURNING agency_id"
        )
        aid = a["agency_id"]
        await _insert_update(conn, aid, "2026-04-01")  # a completed day, well in the past
        # Simulate "still ingesting today": a row captured right now.
        await conn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, 'f_now.pb', now(), 'trip_now', '平日', $2, 'R1', 1, 60)",
            aid,
            time(11, 37),
        )

        from tests.conftest import mirror_updates_to_ch

        mirror_updates_to_ch(ch_client, aid)

        result = await aggregate_freshness(conn, ch_async_client)

    row = next(r for r in result if r.agency_id == aid)
    assert row.data_to == "2026-04-01"  # latest COMPLETED day, not None
    assert row.is_stale is True  # no agg_route_daily row at all → stale
    assert row.agg_behind_days == 1


@pytest.mark.asyncio
async def test_aggregate_freshness_degrades_only_failing_agency_on_ch_error(health_pool, ch_async_client, monkeypatch):
    """A ClickHouse probe failure for ONE agency must not blank the whole
    result or crash the whole call — same "degrade the one CH-derived
    sub-check, keep going" shape as pipeline.reports.network.compute_network_summary.

    The failing agency's CH-derived fields (data_to/is_stale/agg_behind_days)
    degrade to unknown/not-stale, but its Postgres-sourced fields
    (last_analyzed_at, analyze_age_hours, clamp_pct) still come through, and
    every OTHER agency is completely unaffected.
    """
    import api.clickhouse as ch_mod

    async with health_pool.acquire() as conn:
        good = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Good', 'http://good') RETURNING agency_id"
        )
        good_id = good["agency_id"]
        bad = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Bad', 'http://bad') RETURNING agency_id"
        )
        bad_id = bad["agency_id"]
        analyzed = datetime.now(timezone.utc) - timedelta(hours=2)
        await conn.execute("INSERT INTO agg_meta (agency_id, analyzed_at) VALUES ($1, $2)", bad_id, analyzed)
        await conn.execute(
            "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) "
            "VALUES ($1, (now() AT TIME ZONE 'Asia/Tokyo')::date, 100, 5)",
            bad_id,
        )

        real_fn = ch_mod.max_captured_at_before

        async def flaky(ch, agency_id, before):
            if agency_id == bad_id:
                raise RuntimeError("simulated ClickHouse failure")
            return await real_fn(ch, agency_id, before)

        monkeypatch.setattr(ch_mod, "max_captured_at_before", flaky)

        result = await aggregate_freshness(conn, ch_async_client)

    bad_row = next(r for r in result if r.agency_id == bad_id)
    good_row = next(r for r in result if r.agency_id == good_id)

    # Failing agency: CH-derived fields degrade to unknown/not-stale...
    assert bad_row.data_to is None
    assert bad_row.is_stale is False
    assert bad_row.agg_behind_days == 0
    # ...but its Postgres-sourced fields still come through.
    assert bad_row.last_analyzed_at is not None
    assert bad_row.analyze_age_hours is not None
    assert 1.5 < bad_row.analyze_age_hours < 2.5
    assert bad_row.clamp_pct == 5.0

    # The other agency is completely unaffected by the first agency's failure.
    assert good_row.data_to is None
    assert good_row.is_stale is False


@pytest.mark.asyncio
async def test_aggregate_freshness_excludes_deleted_agency(health_pool, ch_client, ch_async_client):
    async with health_pool.acquire() as conn:
        active = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Active', 'http://active') RETURNING agency_id"
        )
        deleted = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES ('Deleted', 'http://deleted', now()) "
            "RETURNING agency_id"
        )
        await _insert_update(conn, deleted["agency_id"], "2026-04-01")

        from tests.conftest import mirror_updates_to_ch

        mirror_updates_to_ch(ch_client, deleted["agency_id"])

        result = await aggregate_freshness(conn, ch_async_client)

    ids = [r.agency_id for r in result]
    assert active["agency_id"] in ids
    assert deleted["agency_id"] not in ids
