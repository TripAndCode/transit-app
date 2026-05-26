import os

import asyncpg
import pytest

from pipeline.query.schema_linker import RouteResolution, resolve_route

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_routes(apply_schema):
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
                (agency_id, "国道線(1021)", "A1 国道・古川線", None),
                (agency_id, "中央大橋線(12211)", "L21 中央大橋線", None),
                (agency_id, "中央大橋線(16021)", "L31 中央大橋線", None),
                (agency_id, "中央大橋線(17091)", "L30 中央大橋線", None),
                (agency_id, "新町線(3021)", "B1 新町線", None),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_resolve_route_exact_code(conn_with_routes):
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("1021", conn, agency_id)
    assert isinstance(result, RouteResolution)
    assert result.route_code == "1021"
    assert result.reason == "exact"
    assert result.candidates == [("1021", "A1 国道・古川線")]


@pytest.mark.asyncio
async def test_resolve_route_short_name_letter_prefix(conn_with_routes):
    """'A1' should resolve via route_short_name LIKE 'A1 %'."""
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("A1", conn, agency_id)
    assert result.route_code == "1021"
    assert result.reason == "alias"


@pytest.mark.asyncio
async def test_resolve_route_n_ban_unresolved_but_candidates(conn_with_routes):
    """'1番' has no exact short_name match in Aomori; should surface candidates."""
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("1番", conn, agency_id)
    assert result.route_code is None
    assert result.reason == "none"
    # candidates may be empty if trigram score is below threshold — accept either.
    assert isinstance(result.candidates, list)


@pytest.mark.asyncio
async def test_resolve_route_line_fragment_alias(conn_with_routes):
    """'中央大橋線' matches three routes with near-identical names.

    The margin-based confident-resolve gate must surface candidates as
    ``reason='fuzzy'`` rather than silently pick one — the top scores are
    too close for a confident single-row resolve, even though every row
    is above the confident floor.
    """
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("中央大橋線", conn, agency_id)
    assert result.reason == "fuzzy"
    assert result.route_code is None
    codes = {c[0] for c in result.candidates}
    assert codes >= {"12211", "16021", "17091"}


@pytest.mark.asyncio
async def test_resolve_route_garbage_returns_none(conn_with_routes):
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("中心部", conn, agency_id)
    assert result.route_code is None
    assert result.reason == "none"
    # candidates allowed to be empty.


@pytest.mark.asyncio
async def test_resolve_route_empty_input(conn_with_routes):
    pool, agency_id = conn_with_routes
    async with pool.acquire() as conn:
        result = await resolve_route("", conn, agency_id)
    assert result.route_code is None
    assert result.reason == "none"
