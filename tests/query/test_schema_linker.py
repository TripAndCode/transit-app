import asyncpg
import os
import pytest

from pipeline.query.schema_linker import RouteResolution, resolve_route

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_routes(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') "
            "RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) "
            "VALUES ($1, $2, $3, $4)",
            [
                (agency_id, "国道線(1021)",       "A1 国道・古川線",       None),
                (agency_id, "中央大橋線(12211)",  "L21 中央大橋線",        None),
                (agency_id, "中央大橋線(16021)",  "L31 中央大橋線",        None),
                (agency_id, "新町線(3021)",       "B1 新町線",            None),
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
