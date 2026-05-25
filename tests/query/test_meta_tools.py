import asyncpg
import os
import pytest

from api.range import RangeCtx
from pipeline.query.meta_tools import describe_data
from datetime import date

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_seed(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') "
            "RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1,$2,$3)",
            [
                (agency_id, "国道線(1021)",     "A1 国道・古川線"),
                (agency_id, "中央大橋線(12211)", "L21 中央大橋線"),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _ctx():
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.mark.asyncio
async def test_describe_data_routes(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await describe_data(
            {"kind": "routes", "limit": 10}, _ctx(), conn, agency_id, locale="ja"
        )
    assert result.kind == "table"
    assert result.columns == ["route_code", "route_short_name"]
    codes = {row[0] for row in result.rows}
    assert codes == {"1021", "12211"}
    assert "2" in result.summary  # summary mentions the count
