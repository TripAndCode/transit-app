"""Async DAL for the ask_intent_cache table.

Used by the chat orchestrator. Pure CRUD — no LLM, no canonicalization (that
happens upstream).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from pipeline.query.intent import IntentSignature

# Mirror the cap in ``query_log.py`` so a 3000-char adversarial question can't
# pollute the autocomplete chip surface.
_MAX_QUESTION_CHARS = 1000


_COLUMNS = "signature_hash, tool, args, confidence, hit_count, last_question, agency_id, created_at, last_used_at"


async def _fetch(conn: asyncpg.Connection, where: str, *params: Any) -> dict[str, Any] | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM ask_intent_cache WHERE {where}", *params)
    if row is None:
        return None
    out = dict(row)
    # asyncpg returns jsonb as str; parse it back so callers don't have to.
    if isinstance(out.get("args"), str):
        out["args"] = json.loads(out["args"])
    return out


async def lookup(conn: asyncpg.Connection, signature_hash: str, agency_id: int) -> dict[str, Any] | None:
    """Return the cache row (as a dict) or None when the hash isn't cached."""
    return await _fetch(conn, "signature_hash = $1 AND agency_id = $2", signature_hash, agency_id)


async def lookup_by_question(conn: asyncpg.Connection, question: str, agency_id: int) -> dict[str, Any] | None:
    """Return a cache row matching the exact question text, or None.

    Used as a pre-LLM optimization: if the same question text was resolved
    before, we can skip the LLM call entirely and dispatch from the cache.
    Paraphrase matching (different text, same canonical intent) is handled by
    the downstream sig-hash lookup after an LLM call.
    """
    return await _fetch(
        conn, "last_question = $1 AND agency_id = $2 ORDER BY last_used_at DESC LIMIT 1", question, agency_id
    )


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
          SET hit_count    = ask_intent_cache.hit_count + 1,
              last_question = EXCLUDED.last_question,
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
