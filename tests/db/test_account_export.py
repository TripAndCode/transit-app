"""The data export (`pipeline/account_export.py`)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.security import token_hash
from pipeline.account_export import collect_user_data
from tests.db.test_account_erasure import _seed_everything, _user

_SECRET_COLUMNS = ("password_hash", "sid_hash", "key_hash", "encrypted_key")


@pytest.mark.asyncio
async def test_a_new_account_exports_empty_collections(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('fresh@x') RETURNING user_id")
    data = await collect_user_data(aconn, uid)
    assert data["profile"]["email"] == "fresh@x"
    assert data["llm_key"] is None
    for key in (
        "identities",
        "sessions",
        "login_events",
        "presets",
        "conversations",
        "api_keys",
        "usage_daily",
        "invites",
        "admin_actions",
        "admin_account_events",
    ):
        assert data[key] == [], key


@pytest.mark.asyncio
async def test_the_export_holds_the_users_data_and_no_secrets(aconn, aagency_id):
    uid = await aconn.fetchval("INSERT INTO users (email, name) VALUES ('full@x', 'Full') RETURNING user_id")
    await aconn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at, ip) VALUES ($1, $2, $3, '10.0.0.1')",
        token_hash("s"),
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    await aconn.execute(
        "INSERT INTO filter_presets (user_id, agency_id, name, range_ctx) VALUES ($1, $2, 'p', '{\"dow\": \"all\"}')",
        uid,
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO user_llm_keys (user_id, provider, encrypted_key, key_suffix)"
        " VALUES ($1, 'openai', '\\x00', 'wxyz')",
        uid,
    )
    conv = await aconn.fetchval(
        "INSERT INTO ask_conversations (user_id, agency_id, title) VALUES ($1, $2, 't') RETURNING conversation_id",
        uid,
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO ask_conversation_messages (conversation_id, role, rendered_summary) VALUES ($1, 'user', 'q?')",
        conv,
    )
    data = await collect_user_data(aconn, uid)
    assert data["sessions"][0]["ip"] == "10.0.0.1"
    assert data["presets"][0]["range_ctx"] == {"dow": "all"}
    assert data["llm_key"] == {"provider": "openai", "key_suffix": "wxyz"}
    assert data["conversations"][0]["messages"][0]["rendered_summary"] == "q?"
    text = json.dumps(data, default=str)
    assert not any(column in text for column in _SECRET_COLUMNS)


@pytest.mark.asyncio
@pytest.mark.parametrize("issued_as", ["legacy@x", "Legacy@X"])
async def test_the_export_lists_legacy_keys_owned_by_email_that_deletion_would_remove(aconn, issued_as):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('legacy@x') RETURNING user_id")
    await aconn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier, label) VALUES ($1, $2, 'pro', 'old key')",
        token_hash("legacy-own"),
        issued_as,
    )
    data = await collect_user_data(aconn, uid)
    assert [k["label"] for k in data["api_keys"]] == ["old key"]


@pytest.mark.asyncio
async def test_the_export_holds_what_erasure_treats_as_the_users_and_nothing_of_anyone_else(aconn, aagency_id):
    """What erase_user deletes or clears as this account's is in its export: the
    invites addressed to it, and the address and browser it left on the admin
    actions it took. What those rows record about other people is not."""
    email = "alice@x"
    admin_id = await _user(aconn, "boss@x", name="Boss", role="admin")
    uid = await _user(aconn, email, name="Alice")
    await _seed_everything(aconn, aagency_id, uid, admin_id, email)

    data = await collect_user_data(aconn, uid)

    assert [(e["kind"], e["ip"]) for e in data["login_events"]] == [("login", "10.0.0.1")]
    assert [{k: e[k] for k in ("kind", "ip", "user_agent")} for e in data["admin_account_events"]] == [
        {"kind": "role_changed", "ip": "10.0.0.2", "user_agent": "ua2"}
    ]
    assert set(data["admin_account_events"][0]) == {"kind", "provider", "ip", "user_agent", "created_at"}
    assert [{k: a[k] for k in ("action", "target_type", "ip")} for a in data["admin_actions"]] == [
        {"action": "user.updated", "target_type": "user", "ip": "10.0.0.3"}
    ]
    assert set(data["admin_actions"][0]) == {"at", "action", "target_type", "ip"}
    assert [i["email"] for i in data["invites"]] == [email]
    assert set(data["invites"][0]) == {"email", "role", "llm_approved", "created_at", "expires_at", "consumed_at"}
    text = json.dumps(data, default=str)
    assert "boss@x" not in text and "Boss" not in text


@pytest.mark.asyncio
async def test_the_export_lists_every_invite_that_deletion_would_remove(aconn):
    """An invite is this account's when it consumed it or it names the address
    in any case; one addressed to someone else is not."""
    admin_id = await _user(aconn, "boss@x", role="admin")
    uid = await _user(aconn, "casey@x")
    await aconn.execute(
        "INSERT INTO user_invites (email, role, invited_by) VALUES ('Casey@X', 'admin', $1), ('bob@x', 'user', $1)",
        admin_id,
    )
    await aconn.execute(
        "INSERT INTO user_invites (email, invited_by, consumed_at, consumed_user_id)"
        " VALUES ('before-rename@x', $1, now(), $2)",
        admin_id,
        uid,
    )
    data = await collect_user_data(aconn, uid)
    assert sorted(i["email"] for i in data["invites"]) == ["Casey@X", "before-rename@x"]
