"""The data export (`pipeline/account_export.py`)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.security import token_hash
from pipeline.account_export import collect_user_data

_SECRET_COLUMNS = ("password_hash", "sid_hash", "key_hash", "encrypted_key")


@pytest.mark.asyncio
async def test_a_new_account_exports_empty_collections(aconn):
    uid = await aconn.fetchval("INSERT INTO users (email) VALUES ('fresh@x') RETURNING user_id")
    data = await collect_user_data(aconn, uid)
    assert data["profile"]["email"] == "fresh@x"
    assert data["llm_key"] is None
    for key in ("identities", "sessions", "login_events", "presets", "conversations", "api_keys", "usage_daily"):
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
