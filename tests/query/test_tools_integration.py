import os
from datetime import date, datetime, timezone

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query.tools import _is_route_registered, dispatch

DATABASE_URL = os.environ["DATABASE_URL"]


def _analyze_sync(agency_id, ch_client):
    """Build the agg_* tables (JST-pinned, like the real pipeline) so the
    aggregate-backed tool paths have data.

    analyze()'s dedup materialization now reads ClickHouse (Task 6); the
    fixture below seeds Postgres `updates` directly (pre-dating that
    migration), so mirror the same rows into ClickHouse first — see
    tests.conftest.mirror_updates_to_ch."""
    import psycopg2

    from pipeline.analyze import analyze
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    conn.autocommit = False
    try:
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
async def conn_routes(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1 国道・古川線')",
            agency_id,
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _ctx():
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.mark.asyncio
async def test_dispatch_describe_data_routes(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("describe_data", {"kind": "routes"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert any(row[0] == "1021" for row in result.rows)


@pytest.mark.asyncio
async def test_dispatch_route_alias_resolution(conn_routes):
    """route_stats called with 'A1' should resolve to 1021 before SQL hits."""
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "A1"}, _ctx(), conn, agency_id, locale="ja")
    # No observations seeded → empty result, but the 'not registered' message
    # must NOT appear since A1 resolved to a real route_code.
    assert "登録されている系統コード" not in result.summary


@pytest.mark.asyncio
async def test_dispatch_route_unresolved_returns_candidates(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "中心部"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "empty"
    # Either a "もしかして" suggestion or the original "not registered" message
    # is acceptable, as long as it's not a hallucinated SQL run.
    assert "もしかして" in result.summary or "見つかりません" in result.summary or "登録" in result.summary


@pytest.fixture
async def conn_routes_with_alias(apply_schema):
    """Seed routes whose names trigger a trigram match for a deliberately
    similar input — used to assert the 'did you mean' message localises."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) "
            "VALUES ($1, $2, $3, $4)",
            [
                (agency_id, "中央大橋線(12211)", "L21 中央大橋線", None),
                (agency_id, "中央大橋線(16021)", "L31 中央大橋線", None),
                (agency_id, "中央大橋線(17091)", "L30 中央大橋線", None),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_dispatch_did_you_mean_locale_en(conn_routes_with_alias):
    """When the route resolver returns candidates (no confident match), the
    'did you mean' summary must render in English for locale='en'."""
    pool, agency_id = conn_routes_with_alias
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "中央大橋線"}, _ctx(), conn, agency_id, locale="en")
    assert result.kind == "empty"
    # English message: "not found. Did you mean: ..."
    assert "not found" in result.summary
    assert "Did you mean" in result.summary
    # JP phrasing must not leak through.
    assert "もしかして" not in result.summary


@pytest.mark.asyncio
async def test_dispatch_capabilities(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("capabilities", {}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    cats = {k for k, _ in result.pairs}
    assert "meta" in cats


@pytest.fixture
async def conn_two_routes_obs(apply_schema, ch_client):
    """Two routes, only route A has observations — used to assert that the
    time_series tool applies the route filter from args."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, $2, $3)",
            [
                (agency_id, "国道線(1021)", "A1 国道・古川線"),
                (agency_id, "新町線(3021)", "B1 新町線"),
            ],
        )
        # 30 observations for route 1021 only (6/day across 5 days so the
        # HAVING COUNT(*) > 5 gate in compute_trend_series is satisfied);
        # route 3021 has none.
        await conn.executemany(
            "INSERT INTO updates "
            "(agency_id, file_name, trip_id, route_code, stop_sequence, captured_at, "
            " scheduled_time, service_type, dep_delay) "
            "VALUES ($1, $2, $3, $4, $5, $6, '08:00'::time, '平日', 60)",
            [
                (
                    agency_id,
                    f"pb_{d}_{s}",
                    f"T{d}_{s}",
                    "1021",
                    s,
                    datetime(2026, 5, d + 1, 8, 0, 0),
                )
                for d in range(5)
                for s in range(1, 7)  # 6 stop_sequences per day → 6 dedup-unique rows
            ],
        )
    # time_series → compute_trend_series reads agg_daily_trend; build it.
    _analyze_sync(agency_id, ch_client)
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_dispatch_time_series_applies_route_filter(conn_two_routes_obs):
    """time_series called with a route arg must narrow compute_trend_series to
    only that route's observations (regression for missing ctx.routes push)."""
    pool, agency_id = conn_two_routes_obs
    async with pool.acquire() as conn:
        # First: series without a route arg returns all-route sample count.
        all_result = await dispatch("time_series", {}, _ctx(), conn, agency_id, locale="ja")
        # Then: series scoped to route 1021 (which is the only one with data).
        route_result = await dispatch("time_series", {"route": "1021"}, _ctx(), conn, agency_id, locale="ja")
    # Same data in both (only one route has data), but the route-filtered
    # call must have actually applied the filter — we verify by also calling
    # for route 3021 (no data) and asserting an empty result.
    assert all_result.kind == "series"
    assert route_result.kind == "series"
    # Both the unfiltered series and the route=1021 series see the same
    # data (only 1021 has observations), so their sample totals must match.
    all_samples = sum(d.get("samples", 0) for d in all_result.series)
    route_samples = sum(d.get("samples", 0) for d in route_result.series)
    assert all_samples > 0
    assert route_samples == all_samples

    async with pool.acquire() as conn:
        empty_result = await dispatch("time_series", {"route": "3021"}, _ctx(), conn, agency_id, locale="ja")
    # Route 3021 has zero observations → empty series. If the route filter
    # were NOT applied this would return the all_result data instead.
    assert empty_result.kind == "empty"


class _ExplodingChClient:
    """Stand-in ``ch`` that fails the test if `_is_route_registered` ever
    reaches the ClickHouse fallback query — used to prove the agg_route_daily
    fast path resolves True on its own, without ever touching ClickHouse."""

    async def query(self, *args, **kwargs):
        raise AssertionError("agg_route_daily fast path should have short-circuited before any ClickHouse query")


@pytest.mark.asyncio
async def test_is_route_registered_fast_path_hits_agg_route_daily_not_clickhouse(aconn, aagency_id):
    """A route already recorded in agg_route_daily (i.e. analyze() has run
    at least once for it) must resolve True from the fast Postgres check
    alone. Demoting the ClickHouse scan to a fallback (rather than removing
    it) must not reintroduce it on the common, already-analyzed-route path —
    proven here via a `ch` stub that raises if queried at all."""
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at) "
        "VALUES ($1, CURRENT_DATE, 'FASTPATH', '平日', 60, 120, 3, 10, now())",
        aagency_id,
    )
    result = await _is_route_registered("FASTPATH", aconn, aagency_id, ch=_ExplodingChClient())
    assert result is True


@pytest.mark.asyncio
async def test_is_route_registered_falls_back_to_clickhouse_when_agg_stale(
    aconn, aagency_id, ch_client, ch_async_client
):
    """A route with live observations already in ClickHouse but nothing yet
    in agg_route_daily (e.g. a brand-new agency/route whose analyze() run
    hasn't happened yet, or failed — api/routers/internal.py's cron loop
    deliberately tolerates per-agency analyze() failures) must still resolve
    True via the ClickHouse fallback rather than a false 'not registered'."""
    from pipeline.clickhouse import insert_updates

    insert_updates(
        ch_client,
        aagency_id,
        [
            (
                "raw_fallback.pb",
                datetime.now(timezone.utc),
                "trip_raw",
                "平日",
                "10:00:00",
                "RAWFALLBACK",
                1,
                60,
            )
        ],
    )
    result = await _is_route_registered("RAWFALLBACK", aconn, aagency_id, ch=ch_async_client)
    assert result is True


@pytest.mark.asyncio
async def test_is_route_registered_returns_false_when_absent_everywhere(aconn, aagency_id, ch_client, ch_async_client):
    """A route absent from static_routes, agg_route_daily, AND raw ClickHouse
    `updates` is genuinely unregistered → False."""
    result = await _is_route_registered("NEVER_SEEN", aconn, aagency_id, ch=ch_async_client)
    assert result is False
