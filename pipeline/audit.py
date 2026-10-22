"""Audit event writer for login_events. One INSERT, no transaction —
caller controls the surrounding txn if it needs atomicity with another write.
"""

import ipaddress
import json
from typing import Any

import asyncpg


def to_inet(ip: str | None) -> str | None:
    """Return `ip` as-is if it parses as an IP address, else `None`.

    A value that fails `::inet` at insert time would raise before the audit
    row -- and the action it describes -- can commit, so anything unparsable
    (a proxy that forwarded garbage) is dropped here instead.
    """
    if ip is None:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


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
    """Insert one audit row into ``login_events``. Caller owns the transaction.
    An unparsable ``ip`` is stored as ``NULL`` rather than failing the insert."""
    await conn.execute(
        """
        INSERT INTO login_events (user_id, actor_id, kind, provider, ip, user_agent, meta)
        VALUES ($1, $2, $3, $4, $5::inet, $6, $7::jsonb)
        """,
        user_id,
        actor_id,
        kind,
        provider,
        to_inet(ip),
        user_agent,
        json.dumps(meta) if meta is not None else None,
    )
