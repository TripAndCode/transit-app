from datetime import date

import pytest

from api.activity import ActivityBuffer, ActivityKey, flush
from tests.conftest import _test_pool


@pytest.mark.asyncio
async def test_two_flushes_of_the_same_key_add_up(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('flush1@x') RETURNING user_id")
    key = ActivityKey(uid, date(2026, 1, 5), "/api/me", "GET", None, False)
    pool = await _test_pool()
    try:
        for _ in range(2):
            buf = ActivityBuffer()
            buf.record(key, is_error=False)
            buf.record(key, is_error=True)
            assert await flush(buf, pool) == 1
    finally:
        await pool.close()
    row = await aconn.fetchrow("SELECT requests, errors FROM user_activity_daily WHERE user_id = $1", uid)
    assert (row["requests"], row["errors"]) == (4, 2)


@pytest.mark.asyncio
async def test_a_deleted_user_does_not_sink_everyone_elses_counts(aconn):
    kept = await aconn.fetchval("INSERT INTO users (email) VALUES ('flush2@x') RETURNING user_id")
    gone = await aconn.fetchval("INSERT INTO users (email) VALUES ('flush3@x') RETURNING user_id")
    buf = ActivityBuffer()
    buf.record(ActivityKey(kept, date(2026, 1, 5), "/api/me", "GET", None, False), is_error=False)
    buf.record(ActivityKey(gone, date(2026, 1, 5), "/api/me", "GET", None, False), is_error=False)
    await aconn.execute("DELETE FROM users WHERE user_id = $1", gone)
    pool = await _test_pool()
    try:
        await flush(buf, pool)
    finally:
        await pool.close()
    assert await aconn.fetchval("SELECT requests FROM user_activity_daily WHERE user_id = $1", kept) == 1
    assert await aconn.fetchval("SELECT count(*) FROM user_activity_daily WHERE user_id = $1", gone) == 0
