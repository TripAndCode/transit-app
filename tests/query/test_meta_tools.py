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


@pytest.mark.asyncio
async def test_describe_data_routes_empty_agency_message(conn_with_seed):
    # Use an agency_id with no routes
    pool, agency_id = conn_with_seed
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "routes"}, _ctx(), conn, agency_id + 99999, locale="ja")
    assert result.kind in ("empty", "table")
    assert "0 路線" not in result.summary  # no awkward "0 路線あります"


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


@pytest.fixture
async def conn_with_observations_ch(conn_with_observations, ch_client, ch_async_client):
    """`conn_with_observations` plus the same rows mirrored into ClickHouse
    and a wired async client — needed by describe_data's `date_range` /
    `sample_counts` / `overview` kinds, which now read live `updates` from
    ClickHouse instead of Postgres (Task 8)."""
    pool, agency_id = conn_with_observations
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    yield pool, agency_id, ch_async_client


@pytest.mark.asyncio
async def test_describe_data_stops(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "stops", "limit": 10}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert len(result.rows) == 5
    assert result.columns == ["stop_id", "stop_name"]


@pytest.mark.asyncio
async def test_describe_data_date_range(conn_with_observations_ch):
    pool, agency_id, ch = conn_with_observations_ch
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "date_range"}, _ctx(), conn, agency_id, locale="ja", ch=ch)
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
async def test_describe_data_agencies_cross_excludes_deleted(conn_with_observations):
    """cross_agency=True must still hide soft-deleted agencies."""
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES ('GONE', 'http://gone', now())"
        )
        result = await describe_data({"kind": "agencies", "cross_agency": True}, _ctx(), conn, agency_id, locale="ja")
    names = {row[1] for row in result.rows}
    assert "GONE" not in names


@pytest.mark.asyncio
async def test_describe_data_agencies_own_deleted_returns_empty(conn_with_observations):
    """A caller whose own agency was soft-deleted gets no rows back."""
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        await conn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = $1", agency_id)
        result = await describe_data({"kind": "agencies"}, _ctx(), conn, agency_id, locale="ja")
    assert result.rows == []


@pytest.mark.asyncio
async def test_describe_data_sample_counts(conn_with_observations_ch):
    pool, agency_id, ch = conn_with_observations_ch
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "sample_counts", "limit": 5}, _ctx(), conn, agency_id, locale="ja", ch=ch)
    assert result.kind == "table"
    assert result.columns == ["route_code", "samples"]
    assert result.rows[0][0] == "1021"
    assert result.rows[0][1] == 30


@pytest.mark.asyncio
async def test_sample_counts_ascending(conn_with_observations_ch):
    pool, agency_id, ch = conn_with_observations_ch
    async with pool.acquire() as conn:
        result = await describe_data(
            {"kind": "sample_counts", "order": "asc", "limit": 5}, _ctx(), conn, agency_id, locale="ja", ch=ch
        )
    assert result.kind == "table"
    # ascending → smallest sample count first
    counts = [row[1] for row in result.rows]
    assert counts == sorted(counts)


@pytest.mark.asyncio
async def test_describe_data_overview(conn_with_observations_ch):
    pool, agency_id, ch = conn_with_observations_ch
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "overview"}, _ctx(), conn, agency_id, locale="ja", ch=ch)
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


@pytest.mark.asyncio
async def test_describe_data_stops_offset(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        page1 = await describe_data({"kind": "stops", "limit": 2, "offset": 0}, _ctx(), conn, agency_id, locale="ja")
        page2 = await describe_data({"kind": "stops", "limit": 2, "offset": 2}, _ctx(), conn, agency_id, locale="ja")
    ids1 = {r[0] for r in page1.rows}
    ids2 = {r[0] for r in page2.rows}
    assert ids1.isdisjoint(ids2)
    assert len(page1.rows) == 2


@pytest.mark.asyncio
async def test_describe_data_offset_past_end_is_empty(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data(
            {"kind": "stops", "limit": 10, "offset": 100000}, _ctx(), conn, agency_id, locale="ja"
        )
    assert len(result.rows) == 0


@pytest.mark.asyncio
async def test_describe_data_offset_negative_clamped(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        result = await describe_data({"kind": "stops", "limit": 2, "offset": -5}, _ctx(), conn, agency_id, locale="ja")
    assert len(result.rows) == 2  # treated as offset 0


@pytest.mark.asyncio
async def test_stops_pagination_stable_under_name_ties(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL, setup=_setup_jst)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        aid = row["agency_id"]
        # 8 stops, only 3 distinct names → ties that would break naive ORDER BY stop_name
        await conn.executemany(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon) VALUES ($1,$2,$3,40.0,140.0)",
            [
                (aid, f"S{i}", name)
                for i, name in enumerate(
                    ["青森駅", "青森駅", "青森駅", "県庁前", "県庁前", "県庁前", "市役所", "市役所"]
                )
            ],
        )
    try:
        async with pool.acquire() as conn:
            p1 = await describe_data({"kind": "stops", "limit": 4, "offset": 0}, _ctx(), conn, aid, locale="ja")
            p2 = await describe_data({"kind": "stops", "limit": 4, "offset": 4}, _ctx(), conn, aid, locale="ja")
        ids1 = [r[0] for r in p1.rows]
        ids2 = [r[0] for r in p2.rows]
        assert len(ids1) == 4 and len(ids2) == 4
        assert set(ids1).isdisjoint(set(ids2)), "pages overlap — pagination unstable"
        assert len(set(ids1) | set(ids2)) == 8, "rows lost across pages"
    finally:
        async with pool.acquire() as c:
            await c.execute("TRUNCATE agencies CASCADE")
        await pool.close()


@pytest.mark.asyncio
async def test_stops_offset_past_end_summary_not_inverted(conn_with_observations):
    pool, agency_id = conn_with_observations
    async with pool.acquire() as conn:
        r = await describe_data({"kind": "stops", "limit": 10, "offset": 100000}, _ctx(), conn, agency_id, locale="ja")
    assert len(r.rows) == 0
    # the summary must not claim a positive range (e.g. "100001–100000件") when empty
    assert "100001" not in r.summary
