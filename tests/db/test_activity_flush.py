from datetime import date
from types import SimpleNamespace

import pytest

from api import activity
from api.activity import ActivityBuffer, ActivityKey, AnonymousKey, flush, flush_anonymous
from tests.conftest import _test_pool


@pytest.mark.asyncio
async def test_two_flushes_of_the_same_key_add_up(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('flush1@x') RETURNING user_id")
    key = ActivityKey(uid, date(2026, 1, 5), "/api/me", "GET", None)
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
    buf.record(ActivityKey(kept, date(2026, 1, 5), "/api/me", "GET", None), is_error=False)
    buf.record(ActivityKey(gone, date(2026, 1, 5), "/api/me", "GET", None), is_error=False)
    await aconn.execute("DELETE FROM users WHERE user_id = $1", gone)
    pool = await _test_pool()
    try:
        await flush(buf, pool)
    finally:
        await pool.close()
    assert await aconn.fetchval("SELECT requests FROM user_activity_daily WHERE user_id = $1", kept) == 1
    assert await aconn.fetchval("SELECT count(*) FROM user_activity_daily WHERE user_id = $1", gone) == 0


@pytest.mark.asyncio
async def test_two_anonymous_flushes_of_the_same_null_agency_key_add_up(aconn):
    # agency_id is NULL on both flushes: the unique index's COALESCE arbiter
    # must still treat them as the same row rather than never conflicting.
    key = AnonymousKey(date(2026, 1, 5), "/api/agencies", "GET", None)
    pool = await _test_pool()
    try:
        for _ in range(2):
            buf = ActivityBuffer()
            buf.record(key, is_error=False)
            buf.record(key, is_error=True)
            assert await flush_anonymous(buf, pool) == 1
    finally:
        await pool.close()
    row = await aconn.fetchrow(
        "SELECT requests, errors FROM anonymous_activity_daily WHERE route = $1 AND agency_id IS NULL",
        "/api/agencies",
    )
    assert (row["requests"], row["errors"]) == (4, 2)


class _FailingAnonymousPool:
    """Delegates to a real pool, but fails only the anonymous table's write,
    so a test can prove the user flush still happens independently."""

    def __init__(self, pool):
        self._pool = pool
        self.anonymous_write_attempted = False

    async def execute(self, sql, *args):
        if "anonymous_activity_daily" in sql:
            self.anonymous_write_attempted = True
            raise ConnectionError("db down")
        return await self._pool.execute(sql, *args)


@pytest.mark.asyncio
async def test_a_failed_anonymous_flush_does_not_stop_the_user_flush_in_stop(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('flush4@x') RETURNING user_id")
    activity.BUFFER.drain()
    activity.ANONYMOUS_BUFFER.drain()
    activity.BUFFER.record(ActivityKey(uid, date(2026, 1, 5), "/api/me", "GET", None), is_error=False)
    activity.ANONYMOUS_BUFFER.record(AnonymousKey(date(2026, 1, 5), "/api/agencies", "GET", None), is_error=False)
    pool = await _test_pool()
    wrapped = _FailingAnonymousPool(pool)
    try:
        await activity.stop(SimpleNamespace(state=SimpleNamespace(pool=wrapped)))
    finally:
        await pool.close()
    assert wrapped.anonymous_write_attempted is True
    assert await aconn.fetchval("SELECT requests FROM user_activity_daily WHERE user_id = $1", uid) == 1
    assert activity.ANONYMOUS_BUFFER.drain() == {}
