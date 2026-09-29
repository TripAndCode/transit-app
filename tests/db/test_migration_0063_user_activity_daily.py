"""user_activity_daily merges counts per key, NULL agency included, and follows its user."""

import pytest

_UPSERT = """
    INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)
    VALUES ($1, DATE '2026-01-05', '/api/me', 'GET', NULL, false, 1, 0)
    ON CONFLICT (user_id, day, route, method, (COALESCE(agency_id, 0)), via_api_key) DO UPDATE SET
        requests = user_activity_daily.requests + EXCLUDED.requests
"""


@pytest.mark.asyncio
async def test_a_null_agency_row_merges_instead_of_duplicating(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('act0063@x') RETURNING user_id")
    await aconn.execute(_UPSERT, uid)
    await aconn.execute(_UPSERT, uid)
    rows = await aconn.fetch("SELECT requests FROM user_activity_daily WHERE user_id = $1", uid)
    assert [r["requests"] for r in rows] == [2]


@pytest.mark.asyncio
async def test_rows_go_with_their_user(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('act0063b@x') RETURNING user_id")
    await aconn.execute(_UPSERT, uid)
    await aconn.execute("DELETE FROM users WHERE user_id = $1", uid)
    assert await aconn.fetchval("SELECT count(*) FROM user_activity_daily WHERE user_id = $1", uid) == 0
