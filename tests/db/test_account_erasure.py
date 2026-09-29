"""Hard deletion of a user (`pipeline/account_erasure.py`)."""

from datetime import datetime, timedelta, timezone

import pytest

from api.security import token_hash
from pipeline.account_erasure import USER_FKS, ErasureRefused, erase_user

_DELETE_RULES = {"c": "CASCADE", "n": "SET NULL", "a": "NO ACTION", "r": "RESTRICT", "d": "SET DEFAULT"}


@pytest.mark.asyncio
async def test_every_foreign_key_to_users_has_a_deletion_decision(aconn):
    """A new FK to users fails here until USER_FKS says what deleting a user does to it."""
    rows = await aconn.fetch(
        """
        SELECT cl.relname AS table_name, a.attname AS column_name, c.confdeltype::text AS rule
        FROM pg_constraint c
        JOIN pg_class cl ON cl.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'users'::regclass
        """
    )
    actual = {(r["table_name"], r["column_name"]): _DELETE_RULES[r["rule"]] for r in rows}
    assert actual == USER_FKS


async def _user(conn, email, *, name=None, role="user", password_hash=None):
    return await conn.fetchval(
        "INSERT INTO users (email, name, role, password_hash) VALUES ($1, $2, $3, $4) RETURNING user_id",
        email,
        name,
        role,
        password_hash,
    )


async def _seed_everything(conn, agency_id, uid, admin_id, email):
    now = datetime.now(timezone.utc)
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at, ip, user_agent) VALUES ($1, $2, $3, '10.0.0.1', 'ua')",
        token_hash(f"s-{uid}"),
        uid,
        now + timedelta(days=1),
    )
    await conn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id) VALUES ('google', $1, $2)", f"g{uid}", uid
    )
    await conn.execute(
        "INSERT INTO filter_presets (user_id, agency_id, name, range_ctx) VALUES ($1, $2, 'mine', '{}')", uid, agency_id
    )
    await conn.execute(
        "INSERT INTO user_llm_keys (user_id, provider, encrypted_key, key_suffix)"
        " VALUES ($1, 'gemini', '\\x00', 'abcd')",
        uid,
    )
    conv = await conn.fetchval(
        "INSERT INTO ask_conversations (user_id, agency_id, title) VALUES ($1, $2, 'q') RETURNING conversation_id",
        uid,
        agency_id,
    )
    await conn.execute(
        "INSERT INTO ask_conversation_messages (conversation_id, role, rendered_summary) VALUES ($1, 'user', 'hello')",
        conv,
    )
    await conn.execute(
        "INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)"
        " VALUES ($1, current_date, '/api/me', 'GET', NULL, false, 1, 0)",
        uid,
    )
    await conn.execute(
        "INSERT INTO login_events (user_id, kind, ip, user_agent) VALUES ($1, 'login', '10.0.0.1', 'ua')", uid
    )
    await conn.execute(
        "INSERT INTO login_events (user_id, actor_id, kind, ip, user_agent)"
        " VALUES ($1, $2, 'role_changed', '10.0.0.2', 'ua2')",
        admin_id,
        uid,
    )
    await conn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier, owner_user_id) VALUES ($1, $2, 'pro', $3)",
        token_hash(f"k-{uid}"),
        email,
        uid,
    )
    await conn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier) VALUES ($1, $2, 'pro')", token_hash(f"legacy-{uid}"), email
    )
    await conn.execute("INSERT INTO user_invites (email, invited_by) VALUES ($1, $2)", email, admin_id)
    await conn.execute(
        "INSERT INTO admin_audit (actor_id, action, target_type, target_id, before, after)"
        " VALUES ($1, 'user.updated', 'user', $2, jsonb_build_object('email', $3::text, 'name', 'Alice'), '{}')",
        admin_id,
        str(uid),
        email,
    )
    await conn.execute(
        "INSERT INTO admin_audit (actor_id, action, target_type, target_id, after)"
        " VALUES ($1, 'invite.created', 'invite', '9', jsonb_build_object('email', $2::text))",
        admin_id,
        email,
    )
    await conn.execute(
        "INSERT INTO admin_audit (actor_id, action, target_type, target_id, ip)"
        " VALUES ($1, 'user.updated', 'user', $2, '10.0.0.3')",
        uid,
        str(admin_id),
    )


@pytest.mark.asyncio
async def test_erase_user_removes_their_personal_data_and_nothing_of_anyone_else(aconn, aagency_id):
    email = "alice@x"
    admin_id = await _user(aconn, "boss@x", name="Boss", role="admin")
    uid = await _user(aconn, email, name="Alice")
    await _seed_everything(aconn, aagency_id, uid, admin_id, email)

    async with aconn.transaction():
        await erase_user(aconn, uid)

    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 0
    for table in (
        "sessions",
        "oauth_identities",
        "filter_presets",
        "user_llm_keys",
        "ask_conversations",
        "user_activity_daily",
    ):
        assert await aconn.fetchval(f"SELECT count(*) FROM {table} WHERE user_id = $1", uid) == 0, table
    assert await aconn.fetchval("SELECT count(*) FROM ask_conversation_messages") == 0
    assert await aconn.fetchval("SELECT count(*) FROM login_events WHERE user_id = $1", uid) == 0
    acted = await aconn.fetchrow(
        "SELECT ip, user_agent FROM login_events WHERE user_id = $1 AND kind = 'role_changed'", admin_id
    )
    assert acted is not None and acted["ip"] is None and acted["user_agent"] is None
    assert await aconn.fetchval("SELECT count(*) FROM api_keys WHERE lower(owner_email) = $1", email) == 0
    assert await aconn.fetchval("SELECT count(*) FROM user_invites WHERE lower(email) = $1", email) == 0
    audit_text = " ".join(
        r["t"]
        for r in await aconn.fetch(
            "SELECT coalesce(before::text, '') || coalesce(after::text, '') AS t FROM admin_audit"
        )
    )
    assert email not in audit_text and "Alice" not in audit_text
    assert await aconn.fetchval("SELECT count(*) FROM admin_audit WHERE ip IS NOT NULL") == 0
    assert await aconn.fetchval("SELECT count(*) FROM admin_audit") == 3
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", admin_id) == 1
    assert (
        await aconn.fetchval(
            "SELECT count(*) FROM login_events WHERE kind = 'deleted' AND user_id IS NULL AND meta->>'self' = 'true'"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_the_last_active_admin_cannot_erase_themselves(aconn):
    uid = await _user(aconn, "only-admin@x", role="admin")
    with pytest.raises(ErasureRefused) as exc:
        async with aconn.transaction():
            await erase_user(aconn, uid)
    assert exc.value.reason == "last_admin"
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 1


@pytest.mark.asyncio
async def test_the_break_glass_local_admin_cannot_be_erased(aconn):
    await _user(aconn, "other-admin@x", role="admin")
    uid = await _user(aconn, "local@x", role="admin", password_hash="x")
    with pytest.raises(ErasureRefused) as exc:
        async with aconn.transaction():
            await erase_user(aconn, uid)
    assert exc.value.reason == "managed_account"


@pytest.mark.asyncio
async def test_another_users_legacy_key_survives_even_if_their_email_differs_only_in_case(aconn):
    await _user(aconn, "boss2@x", role="admin")
    uid = await _user(aconn, "casey@x")
    await _user(aconn, "CASEY@x")
    await aconn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier) VALUES ($1, 'CASEY@x', 'pro')", token_hash("legacy-other")
    )
    async with aconn.transaction():
        await erase_user(aconn, uid)
    assert await aconn.fetchval("SELECT count(*) FROM api_keys WHERE owner_email = 'CASEY@x'") == 1
