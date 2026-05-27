import os

import asyncpg
import pytest

from pipeline.query.query_log import log_query

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_agency(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_log_query_inserts_one_row(conn_agency):
    pool, agency_id = conn_agency
    async with pool.acquire() as conn:
        await log_query(conn, agency_id, "どんな路線がある？", "rules", "describe_data", True)
        row = await conn.fetchrow(
            "SELECT agency_id, question, router_stage, tool, success FROM ask_query_log WHERE agency_id=$1",
            agency_id,
        )
    assert row["question"] == "どんな路線がある？"
    assert row["router_stage"] == "rules"
    assert row["tool"] == "describe_data"
    assert row["success"] is True


@pytest.mark.asyncio
async def test_log_query_swallows_db_error():
    """A failing connection must NOT raise — logging can't break the response."""

    class _BadConn:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    await log_query(_BadConn(), 1, "q", "llm", None, False)  # must complete without raising
