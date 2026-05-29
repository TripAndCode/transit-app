"""Async DAL for the ask_intent_cache table.

Used by the chat orchestrator (T5) and the promotion job (T7). Pure CRUD —
no LLM, no embedding, no canonicalization (those happen upstream).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from pipeline.query.intent import IntentSignature

_ALLOWED_ACTIONS = frozenset({"confirmed", "edited"})


async def lookup(conn: asyncpg.Connection, signature_hash: str, agency_id: int) -> dict[str, Any] | None:
    """Return the cache row (as a dict) or None when the hash isn't cached."""
    row = await conn.fetchrow(
        "SELECT signature_hash, tool, args, confidence, hit_count, last_question, "
        "last_user_action, promoted_at, agency_id, created_at, last_used_at "
        "FROM ask_intent_cache WHERE signature_hash = $1 AND agency_id = $2",
        signature_hash,
        agency_id,
    )
    if row is None:
        return None
    out = dict(row)
    # asyncpg returns jsonb as str; parse it back so callers don't have to.
    if isinstance(out.get("args"), str):
        out["args"] = json.loads(out["args"])
    return out


async def lookup_by_question(conn: asyncpg.Connection, question: str, agency_id: int) -> dict[str, Any] | None:
    """Return a cache row matching the exact question text, or None.

    Used as a pre-LLM optimization: if the same question text was resolved
    before, we can skip the LLM call entirely and dispatch from the cache.
    Paraphrase matching (different text, same canonical intent) is handled by
    the downstream sig-hash lookup after an LLM call.
    """
    row = await conn.fetchrow(
        "SELECT signature_hash, tool, args, confidence, hit_count, last_question, "
        "last_user_action, promoted_at, agency_id, created_at, last_used_at "
        "FROM ask_intent_cache WHERE last_question = $1 AND agency_id = $2 "
        "ORDER BY last_used_at DESC LIMIT 1",
        question,
        agency_id,
    )
    if row is None:
        return None
    out = dict(row)
    if isinstance(out.get("args"), str):
        out["args"] = json.loads(out["args"])
    return out


async def upsert(
    conn: asyncpg.Connection,
    signature_hash: str,
    signature: IntentSignature,
    canonical_args: dict[str, Any],
    agency_id: int,
    *,
    question: str,
) -> None:
    """Insert a new cache row OR increment hit_count + refresh last_question/used_at."""
    await conn.execute(
        """
        INSERT INTO ask_intent_cache
          (signature_hash, tool, args, confidence, hit_count, last_question, agency_id)
        VALUES ($1, $2, $3::jsonb, $4, 1, $5, $6)
        ON CONFLICT (signature_hash, agency_id) DO UPDATE
          SET hit_count = ask_intent_cache.hit_count + 1,
              last_question = EXCLUDED.last_question,
              last_used_at = now(),
              confidence = EXCLUDED.confidence
        """,
        signature_hash,
        signature.tool,
        json.dumps(canonical_args, ensure_ascii=False, separators=(",", ":")),
        float(signature.confidence),
        question,
        agency_id,
    )


async def update_user_action(
    conn: asyncpg.Connection,
    signature_hash: str,
    agency_id: int,
    action: str,
) -> None:
    """Record the user's verdict on a previously-cached interpretation."""
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {sorted(_ALLOWED_ACTIONS)}; got {action!r}")
    await conn.execute(
        "UPDATE ask_intent_cache SET last_user_action = $3 WHERE signature_hash = $1 AND agency_id = $2",
        signature_hash,
        agency_id,
        action,
    )


async def promotion_candidates(
    conn: asyncpg.Connection,
    agency_id: int,
    *,
    hit_threshold: int = 5,
    quiet_days: int = 7,
) -> list[dict[str, Any]]:
    """Rows that satisfy: >=hit_threshold hits, no `edited` action, >=quiet_days old,
    and not yet promoted. ``confirmed`` action is fine — only ``edited`` blocks."""
    rows = await conn.fetch(
        f"""
        SELECT signature_hash, tool, args, last_question, last_user_action,
               hit_count, created_at
        FROM ask_intent_cache
        WHERE agency_id = $1
          AND hit_count >= $2
          AND (last_user_action IS NULL OR last_user_action = 'confirmed')
          AND created_at <= now() - INTERVAL '{int(quiet_days)} days'
          AND promoted_at IS NULL
        ORDER BY hit_count DESC, created_at ASC
        """,
        agency_id,
        hit_threshold,
    )
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("args"), str):
            d["args"] = json.loads(d["args"])
        out.append(d)
    return out


async def mark_promoted(conn: asyncpg.Connection, signature_hash: str, agency_id: int) -> None:
    """Mark a cache row as promoted to rag_chunks (called by the promotion job)."""
    await conn.execute(
        "UPDATE ask_intent_cache SET promoted_at = now() WHERE signature_hash = $1 AND agency_id = $2",
        signature_hash,
        agency_id,
    )
