"""`POST /api/admin/board/alerts/{key}/ack`: an operator's acknowledgement is
stored server-side, shared with every admin, expires after a week, and is
audited."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.admin_board import alert_key
from api.security import token_hash
from tests.conftest import TEST_ORIGIN, _test_pool


def _key(count: int) -> str:
    alert = {"level": "info", "code": "llm_approvals_pending", "params": {"count": count}, "href": "/admin/users"}
    return alert_key(alert)


ACKED = _key(2)


async def _seed_user(conn, *, role="admin"):
    email = f"u{datetime.now().timestamp()}@x"
    uid = (await conn.fetchrow("INSERT INTO users (email, role) VALUES ($1, $2) RETURNING user_id", email, role))[
        "user_id"
    ]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=30),
    )
    return sid, uid


@pytest.fixture
async def admin_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


async def _ack(client, sid, key=ACKED):
    return await client.post(
        f"/api/admin/board/alerts/{key}/ack", cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )


@pytest.mark.asyncio
async def test_an_acknowledgement_is_stored_for_a_week_under_the_acting_admin(admin_client, aconn):
    sid, uid = await _seed_user(aconn)
    r = await _ack(admin_client, sid)
    assert r.status_code == 204
    row = await aconn.fetchrow(
        "SELECT acked_by, expires_at - acked_at AS window FROM admin_alert_acks WHERE alert_key = $1", ACKED
    )
    assert row["acked_by"] == uid
    assert row["window"] == timedelta(days=7)


@pytest.mark.asyncio
async def test_acknowledging_again_renews_the_one_row(admin_client, aconn):
    sid, _ = await _seed_user(aconn)
    await _ack(admin_client, sid)
    await aconn.execute(
        "UPDATE admin_alert_acks SET acked_at = acked_at - interval '1 day', expires_at = expires_at - interval '1 day'"
    )
    await _ack(admin_client, sid)
    rows = await aconn.fetch("SELECT expires_at FROM admin_alert_acks WHERE alert_key = $1", ACKED)
    assert len(rows) == 1
    assert rows[0]["expires_at"] > datetime.now(timezone.utc) + timedelta(days=6, hours=23)


@pytest.mark.asyncio
async def test_an_acknowledgement_is_audited(admin_client, aconn):
    sid, uid = await _seed_user(aconn)
    await _ack(admin_client, sid)
    audit = await aconn.fetchrow(
        "SELECT actor_id, action, target_type, target_id FROM admin_audit ORDER BY at DESC LIMIT 1"
    )
    expected = {"actor_id": uid, "action": "board_alert.ack", "target_type": "board_alert", "target_id": ACKED}
    assert dict(audit) == expected


@pytest.mark.asyncio
async def test_only_an_admin_may_acknowledge(admin_client, aconn):
    sid, _ = await _seed_user(aconn, role="user")
    r = await _ack(admin_client, sid)
    assert r.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM admin_alert_acks") == 0


@pytest.mark.asyncio
async def test_a_key_that_is_not_an_alert_key_is_refused(admin_client, aconn):
    sid, _ = await _seed_user(aconn)
    r = await _ack(admin_client, sid, key="not-a-key")
    assert r.status_code == 422
    assert await aconn.fetchval("SELECT count(*) FROM admin_alert_acks") == 0


@pytest.mark.asyncio
async def test_the_board_reads_only_unexpired_acknowledgements(aconn, apply_schema):
    from api.routers.admin import _acked_alert_keys

    lapsed = _key(3)
    await aconn.execute(
        """
        INSERT INTO admin_alert_acks (alert_key, acked_at, expires_at)
        VALUES ($1, now(), now() + interval '7 days'), ($2, now() - interval '8 days', now() - interval '1 day')
        """,
        ACKED,
        lapsed,
    )
    assert await _acked_alert_keys(aconn, [ACKED, lapsed, _key(4)]) == {ACKED}
