"""The personal data the app holds about one user, for their own download.

It is what ``erase_user`` treats as the user's, found by the same predicates:
the rows it deletes, and the user's own part of the rows it only clears, which
is the address and browser recorded on the admin actions they took. The
targets and snapshots of those actions and the sender of an invite describe
other people, so they stay out, as do the address and browser on an event an
admin recorded against this account, which are the admin's. The email and name
that erasure masks in other admins' audit entries are already in ``profile``.

JSONB and INET columns are read as text and parsed here, so the result does
not depend on which codecs the connection has registered. Secret material
(password, session and key hashes, the encrypted BYOK key) is never selected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from pipeline.api_key_ownership import OWNED_BY_USER


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
    # A row with no actor never holds someone else's address: system events
    # record none, and erase_user clears an actor's before their row goes.
    events = await conn.fetch(
        "SELECT kind, provider,"
        " CASE WHEN actor_id IS NULL OR actor_id = user_id THEN host(ip) END AS ip,"
        " CASE WHEN actor_id IS NULL OR actor_id = user_id THEN user_agent END AS user_agent,"
        " created_at FROM login_events WHERE user_id = $1 ORDER BY created_at",
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
    api_keys = await conn.fetch(
        "SELECT label, tier, created_at, expires_at, revoked_at FROM api_keys"
        f" WHERE {OWNED_BY_USER} ORDER BY created_at",
        user_id,
        profile["email"] if profile else None,
    )
    usage = await conn.fetch(
        "SELECT day, route, method, agency_id, via_api_key, requests, errors FROM user_activity_daily"
        " WHERE user_id = $1 ORDER BY day, route, method",
        user_id,
    )
    invites = await conn.fetch(
        "SELECT email, role, llm_approved, created_at, expires_at, consumed_at FROM user_invites"
        " WHERE consumed_user_id = $1 OR lower(email) = lower($2) ORDER BY created_at",
        user_id,
        profile["email"] if profile else None,
    )
    admin_actions = await conn.fetch(
        "SELECT at, action, target_type, host(ip) AS ip FROM admin_audit WHERE actor_id = $1 ORDER BY at, id",
        user_id,
    )
    admin_account_events = await conn.fetch(
        "SELECT kind, provider, host(ip) AS ip, user_agent, created_at FROM login_events"
        " WHERE actor_id = $1 AND user_id IS DISTINCT FROM $1 ORDER BY created_at",
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
        "invites": [dict(r) for r in invites],
        "admin_actions": [dict(r) for r in admin_actions],
        "admin_account_events": [dict(r) for r in admin_account_events],
    }
