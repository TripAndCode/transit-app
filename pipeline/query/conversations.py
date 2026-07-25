"""Async DAL for ask_conversations + ask_conversation_messages."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

_MAX_TITLE = 200
_CONV_COLS = "conversation_id, user_id, agency_id, title, filter_ctx, pinned, created_at, updated_at"


class PermissionDenied(Exception):
    """Raised when a user tries to read or modify a conversation they don't own."""


def _row_to_conv(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("filter_ctx"), str):
        d["filter_ctx"] = json.loads(d["filter_ctx"])
    # Strip our internal _client_id marker if present
    fc = d.get("filter_ctx") or {}
    fc.pop("_client_id", None)
    d["filter_ctx"] = fc
    return d


async def create_conversation(
    conn: asyncpg.Connection,
    *,
    user_id: int | None,
    agency_id: int,
    title: str,
    filter_ctx: dict[str, Any],
) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"INSERT INTO ask_conversations (user_id, agency_id, title, filter_ctx) "
        f"VALUES ($1, $2, $3, $4::jsonb) "
        f"RETURNING {_CONV_COLS}",
        user_id,
        agency_id,
        title[:_MAX_TITLE],
        json.dumps(filter_ctx),
    )
    return _row_to_conv(row)


async def get_conversation(
    conn: asyncpg.Connection, conversation_id: Any, *, user_id: int | None, agency_id: int
) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"SELECT {_CONV_COLS} FROM ask_conversations WHERE conversation_id = $1",
        conversation_id,
    )
    if row is None:
        raise LookupError(f"conversation {conversation_id} not found")
    if row["user_id"] != user_id or row["agency_id"] != agency_id:
        raise PermissionDenied(f"conversation {conversation_id} not owned by user {user_id} in agency {agency_id}")
    return _row_to_conv(row)


async def list_conversations(
    conn: asyncpg.Connection,
    *,
    user_id: int | None,
    agency_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"SELECT {_CONV_COLS} FROM ask_conversations "
        f"WHERE user_id IS NOT DISTINCT FROM $1 AND agency_id = $2 "
        f"ORDER BY pinned DESC, updated_at DESC LIMIT $3",
        user_id,
        agency_id,
        int(limit),
    )
    return [_row_to_conv(r) for r in rows]


async def update_conversation(
    conn: asyncpg.Connection, conversation_id: Any, *, user_id: int | None, agency_id: int, **fields: Any
) -> dict[str, Any]:
    """Update title / pinned / filter_ctx. Raises PermissionDenied on owner mismatch."""
    existing = await conn.fetchrow(
        "SELECT user_id, agency_id FROM ask_conversations WHERE conversation_id = $1", conversation_id
    )
    if existing is None:
        raise LookupError(f"conversation {conversation_id} not found")
    if existing["user_id"] != user_id or existing["agency_id"] != agency_id:
        raise PermissionDenied(f"conversation {conversation_id} not owned by user {user_id} in agency {agency_id}")

    sets: list[str] = []
    params: list[Any] = []
    if "title" in fields:
        params.append(str(fields["title"])[:_MAX_TITLE])
        sets.append(f"title = ${len(params)}")
    if "pinned" in fields:
        params.append(bool(fields["pinned"]))
        sets.append(f"pinned = ${len(params)}")
    if "filter_ctx" in fields:
        params.append(json.dumps(fields["filter_ctx"]))
        sets.append(f"filter_ctx = ${len(params)}::jsonb")
    if not sets:
        return await get_conversation(conn, conversation_id, user_id=user_id, agency_id=agency_id)
    sets.append("updated_at = now()")
    params.append(conversation_id)
    row = await conn.fetchrow(
        f"UPDATE ask_conversations SET {', '.join(sets)} WHERE conversation_id = ${len(params)} RETURNING {_CONV_COLS}",
        *params,
    )
    return _row_to_conv(row)


async def delete_conversation(
    conn: asyncpg.Connection, conversation_id: Any, *, user_id: int | None, agency_id: int
) -> None:
    existing = await conn.fetchrow(
        "SELECT user_id, agency_id FROM ask_conversations WHERE conversation_id = $1", conversation_id
    )
    if existing is None:
        raise LookupError(f"conversation {conversation_id} not found")
    if existing["user_id"] != user_id or existing["agency_id"] != agency_id:
        raise PermissionDenied(f"conversation {conversation_id} not owned by user {user_id} in agency {agency_id}")
    await conn.execute("DELETE FROM ask_conversations WHERE conversation_id = $1", conversation_id)


async def append_message(
    conn: asyncpg.Connection,
    conversation_id: Any,
    *,
    role: str,
    chip_id: str | None,
    tool: str | None,
    args: dict[str, Any] | None,
    signature_hash: str | None,
    result: dict[str, Any] | None,
    rendered_summary: str | None,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO ask_conversation_messages
          (conversation_id, role, chip_id, tool, args, signature_hash, result, rendered_summary)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8)
        RETURNING message_id, conversation_id, role, chip_id, tool, args, signature_hash,
                  result, rendered_summary, created_at
        """,
        conversation_id,
        role,
        chip_id,
        tool,
        json.dumps(args) if args is not None else None,
        signature_hash,
        json.dumps(result) if result is not None else None,
        rendered_summary,
    )
    await conn.execute(
        "UPDATE ask_conversations SET updated_at = now() WHERE conversation_id = $1",
        conversation_id,
    )
    d = dict(row)
    if isinstance(d.get("args"), str):
        d["args"] = json.loads(d["args"])
    if isinstance(d.get("result"), str):
        d["result"] = json.loads(d["result"])
    return d


async def list_messages(
    conn: asyncpg.Connection, conversation_id: Any, *, user_id: int | None, agency_id: int
) -> list[dict[str, Any]]:
    owner_row = await conn.fetchrow(
        "SELECT user_id, agency_id FROM ask_conversations WHERE conversation_id = $1", conversation_id
    )
    if owner_row is None:
        raise LookupError(f"conversation {conversation_id} not found")
    if owner_row["user_id"] != user_id or owner_row["agency_id"] != agency_id:
        raise PermissionDenied(f"conversation {conversation_id} not owned by user {user_id} in agency {agency_id}")
    rows = await conn.fetch(
        """
        SELECT message_id, conversation_id, role, chip_id, tool, args, signature_hash,
               result, rendered_summary, created_at
        FROM ask_conversation_messages
        WHERE conversation_id = $1 ORDER BY message_id
        """,
        conversation_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("args"), str):
            d["args"] = json.loads(d["args"])
        if isinstance(d.get("result"), str):
            d["result"] = json.loads(d["result"])
        out.append(d)
    return out


async def migrate_anon_threads(
    conn: asyncpg.Connection,
    *,
    user_id: int,
    agency_id: int,
    threads: list[dict[str, Any]],
) -> int:
    """Upload anonymous (localStorage) threads into the DB on first sign-in.

    Idempotent via ``client_id`` (stashed in filter_ctx._client_id). If a thread
    with the same (user_id, _client_id) already exists, skip it.
    """
    if not threads:
        return 0
    # Find existing _client_ids for this user
    existing_rows = await conn.fetch(
        "SELECT filter_ctx->>'_client_id' AS cid FROM ask_conversations "
        "WHERE user_id = $1 AND filter_ctx ? '_client_id'",
        user_id,
    )
    existing = {r["cid"] for r in existing_rows if r["cid"]}
    inserted = 0
    for t in threads:
        cid = t.get("client_id")
        if not cid or cid in existing:
            continue
        fc = dict(t.get("filter_ctx") or {})
        fc["_client_id"] = cid
        # Home each thread under its OWN agency (threads span agencies in
        # localStorage); fall back to the request-scope agency for older
        # payloads that predate the per-thread agency_id field.
        try:
            thread_agency = int(t.get("agency_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            thread_agency = agency_id
        await conn.execute(
            "INSERT INTO ask_conversations (user_id, agency_id, title, filter_ctx, pinned) "
            "VALUES ($1, $2, $3, $4::jsonb, $5)",
            user_id,
            thread_agency,
            str(t.get("title", "(no title)"))[:_MAX_TITLE],
            json.dumps(fc),
            bool(t.get("pinned", False)),
        )
        inserted += 1
    return inserted
