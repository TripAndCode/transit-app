"""The `client` fixture's pool must share api.main's session timezone, or SQL
using `now()` disagrees with JST-dated test fixtures for part of every day."""

from tests.conftest import _test_pool


async def test_test_pool_uses_asia_tokyo_like_the_production_pool(apply_schema):
    pool = await _test_pool()
    try:
        async with pool.acquire() as conn:
            tz = await conn.fetchval("SHOW TimeZone")
    finally:
        await pool.close()
    assert tz == "Asia/Tokyo"
