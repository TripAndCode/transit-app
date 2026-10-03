"""The daily prune deletes personal data past the policy's periods and keeps the rest."""

from datetime import date, datetime, timedelta, timezone

import pytest

from api import retention
from api.security import token_hash
from tests.conftest import _test_pool


@pytest.mark.asyncio
async def test_rows_past_retention_go_and_recent_rows_stay(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('prune@x') RETURNING user_id")
    old_day, new_day = date.today() - timedelta(days=800), date.today() - timedelta(days=30)
    for day in (old_day, new_day):
        await aconn.execute(
            "INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)"
            " VALUES ($1, $2, '/api/me', 'GET', NULL, false, 1, 0)",
            uid,
            day,
        )
    now = datetime.now(timezone.utc)
    for at in (now - timedelta(days=800), now - timedelta(days=30)):
        await aconn.execute("INSERT INTO login_events (user_id, kind, created_at) VALUES ($1, 'login', $2)", uid, at)
    for name, expires in (("expired", now - timedelta(days=1)), ("live", now + timedelta(days=1))):
        await aconn.execute(
            "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)", token_hash(name), uid, expires
        )

    pool = await _test_pool()
    try:
        await retention.prune_once(pool)
    finally:
        await pool.close()

    assert [r["day"] for r in await aconn.fetch("SELECT day FROM user_activity_daily WHERE user_id = $1", uid)] == [
        new_day
    ]
    assert await aconn.fetchval("SELECT count(*) FROM login_events WHERE user_id = $1", uid) == 1
    assert [r["sid_hash"] for r in await aconn.fetch("SELECT sid_hash FROM sessions WHERE user_id = $1", uid)] == [
        token_hash("live")
    ]
