"""The daily prune deletes personal data past the policy's periods and keeps the rest."""

from datetime import date, datetime, timedelta, timezone

import pytest

from api import retention
from api.security import token_hash
from tests.conftest import _test_pool
from tests.fixtures.users import add_session


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
    await add_session(aconn, uid, expires_in=timedelta(days=-1))
    live = await add_session(aconn, uid, expires_in=timedelta(days=1))

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
        token_hash(live)
    ]


@pytest.mark.asyncio
async def test_intent_cache_questions_past_retention_go_unless_promoted(aconn, aagency_id):
    now = datetime.now(timezone.utc)
    rows = {
        "a" * 16: (now - timedelta(days=120), None),
        "b" * 16: (now - timedelta(days=30), None),
        "c" * 16: (now - timedelta(days=120), now - timedelta(days=200)),
    }
    for sig, (last_used_at, promoted_at) in rows.items():
        await aconn.execute(
            "INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence, hit_count, last_question,"
            " agency_id, created_at, last_used_at, promoted_at)"
            " VALUES ($1, 'route_delays', '{}'::jsonb, 0.9, 1, 'a question', $2, $3, $3, $4)",
            sig,
            aagency_id,
            last_used_at,
            promoted_at,
        )

    pool = await _test_pool()
    try:
        await retention.prune_once(pool)
    finally:
        await pool.close()

    kept = await aconn.fetch("SELECT signature_hash FROM ask_intent_cache ORDER BY signature_hash")
    assert [r["signature_hash"] for r in kept] == ["b" * 16, "c" * 16]


@pytest.mark.asyncio
async def test_an_admin_actions_address_and_browser_go_after_the_admin_audit_period(aconn):
    """The login-history row an admin's action leaves outlives the admin action
    log, so what it records about the admin is cleared at that log's period.
    The account's own rows, and ones with no actor, keep theirs."""
    admin_id = await aconn.fetchval("INSERT INTO users (email, role) VALUES ('boss@x', 'admin') RETURNING user_id")
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('target@x') RETURNING user_id")
    now = datetime.now(timezone.utc)
    old, recent = now - timedelta(days=500), now - timedelta(days=30)
    rows = {
        "old_admin": (uid, admin_id, "suspended", old),
        "old_admin_no_target": (None, admin_id, "agency_created", old),
        "recent_admin": (uid, admin_id, "role_changed", recent),
        "old_self": (uid, uid, "login", old),
        "old_no_actor": (uid, None, "role_changed", old),
    }
    for label, (user_id, actor_id, kind, at) in rows.items():
        await aconn.execute(
            "INSERT INTO login_events (user_id, actor_id, kind, ip, user_agent, created_at, meta)"
            " VALUES ($1, $2, $3, '10.0.0.9', 'ua', $4, jsonb_build_object('label', $5::text))",
            user_id,
            actor_id,
            kind,
            at,
            label,
        )

    pool = await _test_pool()
    try:
        await retention.prune_once(pool)
    finally:
        await pool.close()

    kept = {
        r["label"]: (r["ip"], r["user_agent"])
        for r in await aconn.fetch(
            "SELECT meta->>'label' AS label, host(ip) AS ip, user_agent FROM login_events WHERE meta ? 'label'"
        )
    }
    assert kept == {
        "old_admin": (None, None),
        "old_admin_no_target": (None, None),
        "recent_admin": ("10.0.0.9", "ua"),
        "old_self": ("10.0.0.9", "ua"),
        "old_no_actor": ("10.0.0.9", "ua"),
    }
