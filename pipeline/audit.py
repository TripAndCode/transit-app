"""Audit event writer for login_events. One INSERT, no transaction —
caller controls the surrounding txn if it needs atomicity with another write.
"""

import json
from typing import Any

import asyncpg


async def record_event(
    conn: asyncpg.Connection,
    *,
    user_id: int | None,
    kind: str,
    actor_id: int | None = None,
    provider: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row into ``login_events``. Caller owns the transaction."""
    await conn.execute(
        """
        INSERT INTO login_events (user_id, actor_id, kind, provider, ip, user_agent, meta)
        VALUES ($1, $2, $3, $4, $5::inet, $6, $7::jsonb)
        """,
        user_id,
        actor_id,
        kind,
        provider,
        ip,
        user_agent,
        json.dumps(meta) if meta is not None else None,
    )
