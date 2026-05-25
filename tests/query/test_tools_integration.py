import asyncpg
import os
import pytest

from datetime import date
from api.range import RangeCtx
from pipeline.query.tools import dispatch

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_routes(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') "
            "RETURNING agency_id"
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
        result = await dispatch(
            "describe_data", {"kind": "routes"}, _ctx(), conn, agency_id, locale="ja"
        )
    assert result.kind == "table"
    assert any(row[0] == "1021" for row in result.rows)


@pytest.mark.asyncio
async def test_dispatch_route_alias_resolution(conn_routes):
    """route_stats called with 'A1' should resolve to 1021 before SQL hits."""
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch(
            "route_stats", {"route": "A1"}, _ctx(), conn, agency_id, locale="ja"
        )
    # No observations seeded → empty result, but the 'not registered' message
    # must NOT appear since A1 resolved to a real route_code.
    assert "登録されている系統コード" not in result.summary


@pytest.mark.asyncio
async def test_dispatch_route_unresolved_returns_candidates(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch(
            "route_stats", {"route": "中心部"}, _ctx(), conn, agency_id, locale="ja"
        )
    assert result.kind == "empty"
    # Either a "もしかして" suggestion or the original "not registered" message
    # is acceptable, as long as it's not a hallucinated SQL run.
    assert ("もしかして" in result.summary
            or "見つかりません" in result.summary
            or "登録" in result.summary)


@pytest.mark.asyncio
async def test_dispatch_capabilities(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("capabilities", {}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    cats = {k for k, _ in result.pairs}
    assert "meta" in cats
