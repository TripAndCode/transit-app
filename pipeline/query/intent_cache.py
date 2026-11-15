"""Async DAL for the ask_intent_cache table.

Used by the chat orchestrator. Pure CRUD — no LLM, no canonicalization (that
happens upstream).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from pipeline.query.intent import IntentSignature

_ALLOWED_ACTIONS = frozenset({"confirmed", "edited"})

# Mirror the cap in ``query_log.py`` so a 3000-char adversarial question can't
# pollute the autocomplete chip surface.
_MAX_QUESTION_CHARS = 1000


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
    """Insert a new cache row OR increment hit_count + refresh last_question/used_at.

    A row with ``promoted_at`` set keeps its last_question. Nothing sets
    ``promoted_at`` any more, so no row is in that state today; the guard stays
    until the owner decides what happens to the column.
    """
    await conn.execute(
        """
        INSERT INTO ask_intent_cache
          (signature_hash, tool, args, confidence, hit_count, last_question, agency_id)
        VALUES ($1, $2, $3::jsonb, $4, 1, $5, $6)
        ON CONFLICT (signature_hash, agency_id) DO UPDATE
          SET hit_count    = ask_intent_cache.hit_count + 1,
              last_question = CASE WHEN ask_intent_cache.promoted_at IS NULL
                                   THEN EXCLUDED.last_question
                                   ELSE ask_intent_cache.last_question END,
              last_used_at  = now(),
              confidence    = GREATEST(ask_intent_cache.confidence, EXCLUDED.confidence)
        """,
        signature_hash,
        signature.tool,
        json.dumps(canonical_args, ensure_ascii=False, separators=(",", ":")),
        float(signature.confidence),
        question[:_MAX_QUESTION_CHARS],
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
