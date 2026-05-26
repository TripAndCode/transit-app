import os
from datetime import date, datetime

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query.meta_tools import capabilities, describe_data

DATABASE_URL = os.environ["DATABASE_URL"]


async def _setup_jst(conn):
    # asyncpg resets session state between pool acquires, so use ``setup``
    # (per-acquire) rather than ``init`` (once on creation) to mirror
    # api/main.py's per-acquire JST guarantee.
    await conn.execute("SET TIME ZONE 'Asia/Tokyo'")


@pytest.fixture
async def conn_with_seed(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL, setup=_setup_jst)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1,$2,$3)",
            [
                (agency_id, "国道線(1021)", "A1 国道・古川線"),
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
        result = await describe_data({"kind": "routes", "limit": 10}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert result.columns == ["route_code", "route_short_name"]
    codes = {row[0] for row in result.rows}
    assert codes == {"1021", "12211"}
    assert "2" in result.summary  # summary mentions the count


@pytest.mark.asyncio
async def test_describe_data_routes_filter_substring_matches(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await describe_data(
            {"kind": "routes", "filter_substring": "国道"}, _ctx(), conn, agency_id, locale="ja"
        )
    # conn_with_seed has "A1 国道・古川線" — should match exactly 1
    assert result.kind == "table"
    assert len(result.rows) == 1
    assert result.rows[0][0] == "1021"


@pytest.mark.asyncio
async def test_describe_data_routes_filter_no_match_is_empty(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await describe_data(
            {"kind": "routes", "filter_substring": "存在しない路線XYZ"}, _ctx(), conn, agency_id, locale="ja"
        )
    # Must NOT dump all rows. Either empty kind or 0 rows with a clear message.
    assert len(result.rows) == 0


@pytest.fixture
async def conn_with_observations(conn_with_seed):
    """Extends conn_with_seed: also inserts stops + updates so date_range/sample_counts work."""
    pool, agency_id = conn_with_seed
    async with pool.acquire() as c:
        await c.executemany(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon) "
            "VALUES ($1, $2, $3, 40.0, 140.0)",
            [(agency_id, f"S{i}", f"停留所{i}") for i in range(5)],
        )
        await c.executemany(
            "INSERT INTO updates "
            "(agency_id, file_name, trip_id, route_code, stop_sequence, captured_at, "
            " scheduled_time, service_type, dep_delay) "
            "VALUES ($1, $2, $3, $4, 1, $5, '08:00'::time, '平日', 60)",
            [
                (
                    agency_id,
                    f"pb_{i}",
                    f"T{i}",
                    "1021",
                    datetime(2026, 5, (i % 26) + 1, 8, 0, 0),
                )
                for i in range(30)
            ],
        )
    yield pool, agency_id


@pytest.mark.asyncio
async def test_describe_data_stops(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "stops", "limit": 10}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert len(result.rows) == 5
    assert result.columns == ["stop_id", "stop_name"]


@pytest.mark.asyncio
async def test_describe_data_date_range(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "date_range"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    keys = dict(result.pairs)
    assert "first_observed" in keys
    assert "last_observed" in keys
    assert "distinct_days" in keys


@pytest.mark.asyncio
async def test_describe_data_agencies(conn_with_observations):
    """Default behavior must single-tenant: only the caller's own agency."""
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        # Seed a second tenant the caller shouldn't see by default.
        await conn.execute("INSERT INTO agencies (agency_name, feed_url) VALUES ('OTHER', 'http://other')")
        result = await describe_data({"kind": "agencies"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert result.columns == ["agency_id", "agency_name"]
    # Only the caller's own row — the OTHER agency must be hidden.
    assert len(result.rows) == 1
    assert result.rows[0][0] == agency_id


@pytest.mark.asyncio
async def test_describe_data_agencies_cross(conn_with_observations):
    """When cross_agency=True, every agency is returned (admin path)."""
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO agencies (agency_name, feed_url) VALUES ('OTHER', 'http://other')")
        result = await describe_data({"kind": "agencies", "cross_agency": True}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    names = {row[1] for row in result.rows}
    assert "OTHER" in names
    assert len(result.rows) >= 2


@pytest.mark.asyncio
async def test_describe_data_sample_counts(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "sample_counts", "limit": 5}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert result.columns == ["route_code", "samples"]
    assert result.rows[0][0] == "1021"
    assert result.rows[0][1] == 30


@pytest.mark.asyncio
async def test_describe_data_overview(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "overview"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    keys = dict(result.pairs)
    assert keys.get("routes")
    assert keys.get("stops")
    assert keys.get("observations")


@pytest.mark.asyncio
async def test_describe_data_metrics(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "metrics"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    assert any(k == "avg_delay" for k, _ in result.pairs)
    # JP labels include 遅延.
    avg_delay_value = dict(result.pairs)["avg_delay"]
    assert "遅延" in avg_delay_value


@pytest.mark.asyncio
async def test_describe_data_metrics_locale_en(conn_with_observations):
    """The metric-list value strings must switch to English when locale='en'."""
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "metrics"}, _ctx(), conn, agency_id, locale="en")
    assert result.kind == "kv"
    pairs = dict(result.pairs)
    assert "avg_delay" in pairs
    # At least one value must contain English text rather than the JP labels.
    assert "delay" in pairs["avg_delay"]
    assert "遅延" not in pairs["avg_delay"]


@pytest.mark.asyncio
async def test_describe_data_invalid_limit_falls_back(conn_with_seed):
    """A non-numeric `limit` from the LLM must not crash dispatch — coerce
    to the default and return a valid table."""
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "routes", "limit": "abc"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    # Two seeded routes are well under the fallback default (50).
    assert len(result.rows) == 2


@pytest.mark.asyncio
async def test_describe_data_invalid_kind(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "nope"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "empty"
    assert "nope" in result.summary


@pytest.mark.asyncio
async def test_capabilities_all_categories(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await capabilities({}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    cats = {k for k, _ in result.pairs}
    assert cats >= {
        "single_route",
        "ranking",
        "comparison",
        "trend",
        "on_time",
        "stop_level",
        "meta",
    }


@pytest.mark.asyncio
async def test_capabilities_specific_category(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await capabilities({"category": "ranking"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    assert len(result.pairs) == 1
    key, examples = result.pairs[0]
    assert key == "ranking"
    assert "ワースト" in examples or "TOP" in examples


@pytest.mark.asyncio
async def test_capabilities_unknown_category_returns_all(conn_with_seed):
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await capabilities({"category": "nope"}, _ctx(), conn, agency_id, locale="ja")
    assert len(result.pairs) >= 7
