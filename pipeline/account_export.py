"""Everything the app holds about one user, for their own download.

JSONB and INET columns are read as text and parsed here, so the result does
not depend on which codecs the connection has registered. Secret material
(password, session and key hashes, the encrypted BYOK key) is never selected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg


def _parsed(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


async def collect_user_data(conn: asyncpg.Connection, user_id: int) -> dict[str, Any]:
    profile = await conn.fetchrow(
        "SELECT user_id, email, name, avatar_url, role, llm_approved, created_at FROM users WHERE user_id = $1", user_id
    )
    identities = await conn.fetch(
        "SELECT provider, email_at_link, created_at FROM oauth_identities WHERE user_id = $1 ORDER BY created_at",
        user_id,
    )
    sessions = await conn.fetch(
        "SELECT created_at, last_seen_at, expires_at, host(ip) AS ip, user_agent FROM sessions"
        " WHERE user_id = $1 ORDER BY created_at",
        user_id,
    )
    events = await conn.fetch(
        "SELECT kind, provider, host(ip) AS ip, user_agent, created_at FROM login_events"
        " WHERE user_id = $1 ORDER BY created_at",
        user_id,
    )
    presets = await conn.fetch(
        "SELECT agency_id, name, range_ctx::text AS range_ctx, created_at FROM filter_presets"
        " WHERE user_id = $1 ORDER BY created_at",
        user_id,
    )
    conversations = await conn.fetch(
        "SELECT conversation_id::text AS conversation_id, agency_id, title, filter_ctx::text AS filter_ctx, pinned,"
        " created_at, updated_at FROM ask_conversations WHERE user_id = $1 ORDER BY created_at",
        user_id,
    )
    messages = await conn.fetch(
        "SELECT m.conversation_id::text AS conversation_id, m.role, m.chip_id, m.tool, m.args::text AS args,"
        " m.result::text AS result, m.rendered_summary, m.conditions::text AS conditions, m.created_at"
        " FROM ask_conversation_messages m JOIN ask_conversations c USING (conversation_id)"
        " WHERE c.user_id = $1 ORDER BY m.message_id",
        user_id,
    )
    llm_key = await conn.fetchrow("SELECT provider, key_suffix FROM user_llm_keys WHERE user_id = $1", user_id)
    # The same ownership erase_user deletes by: issued to the account, or a
    # legacy ownerless key recorded under its exact email.
    api_keys = await conn.fetch(
        "SELECT label, tier, created_at, expires_at, revoked_at FROM api_keys"
        " WHERE owner_user_id = $1 OR (owner_user_id IS NULL AND owner_email = $2) ORDER BY created_at",
        user_id,
        profile["email"] if profile else None,
    )
    usage = await conn.fetch(
        "SELECT day, route, method, agency_id, via_api_key, requests, errors FROM user_activity_daily"
        " WHERE user_id = $1 ORDER BY day, route, method",
        user_id,
    )

    by_conversation: dict[str, list[dict[str, Any]]] = {}
    for m in messages:
        by_conversation.setdefault(m["conversation_id"], []).append(
            {
                "role": m["role"],
                "chip_id": m["chip_id"],
                "tool": m["tool"],
                "args": _parsed(m["args"]),
                "result": _parsed(m["result"]),
                "rendered_summary": m["rendered_summary"],
                "conditions": _parsed(m["conditions"]),
                "created_at": m["created_at"],
            }
        )
    return {
        "exported_at": datetime.now(timezone.utc),
        "profile": dict(profile) if profile else None,
        "identities": [dict(r) for r in identities],
        "sessions": [dict(r) for r in sessions],
        "login_events": [dict(r) for r in events],
        "presets": [{**dict(r), "range_ctx": _parsed(r["range_ctx"])} for r in presets],
        "conversations": [
            {
                **{k: v for k, v in dict(c).items() if k != "filter_ctx"},
                "filter_ctx": _parsed(c["filter_ctx"]),
                "messages": by_conversation.get(c["conversation_id"], []),
            }
            for c in conversations
        ],
        "llm_key": dict(llm_key) if llm_key else None,
        "api_keys": [dict(r) for r in api_keys],
        "usage_daily": [dict(r) for r in usage],
    }
