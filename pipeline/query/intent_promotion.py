"""Promotion of ``ask_intent_cache`` rows into ``rag_chunks``.

Core logic shared by ``scripts/promote_intent_cache.py`` (CLI, scheduled
batch job, whole-agency, hit_threshold/quiet_days-gated) and the admin
"promote to intent cache" action (``api/routers/admin_ask.py``, one specific
row, admin-triggered). Kept out of ``pipeline.query.intent_cache`` because
that module is documented as pure CRUD (no LLM, no embedding); this module
does the embedding + ``rag_chunks`` write.

The inserted chunk reuses the same e5 ``passage:`` prefix convention as
``pipeline.query.rag_index`` / ``build_rag_index`` so Stage-2 embedding
nearest-neighbor search can find promoted questions without modification.
``chunk_id`` format is ``cache_<signature_hash>`` (16 hex chars) — unique per
(agency, canonical intent) and self-describing as cache-sourced.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, Protocol

import asyncpg

from pipeline.query import intent_cache
from pipeline.query.rag_index import _format_vec

_log = logging.getLogger(__name__)


class SupportsEmbed(Protocol):
    """The subset of ``pipeline.query.embeddings.Embedder`` this module needs
    — a Protocol so tests can pass a lightweight fake instead of the real
    (model-loading) embedder."""

    available: bool

    def embed(self, text: str, *, mode: Literal["query", "passage"]) -> list[float]: ...


def _content_hash(text: str) -> str:
    """Hex SHA-256 of ``text``, used as the ``rag_chunks.content_hash`` sentinel."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _promote_one(
    conn: asyncpg.Connection,
    embedder: SupportsEmbed,
    candidate: dict[str, Any],
    agency_id: int,
    *,
    dispatchable_tools: Any,
) -> bool:
    """Upsert the ``rag_chunks`` row for one eligible cache candidate and
    mark it promoted.

    Returns ``False`` (no-op, nothing written) when the candidate's stored
    tool isn't in ``dispatchable_tools`` — the LLM sometimes stores
    ``tool="none"`` (or an obsolete name) for out-of-scope questions, and
    promoting those would pollute the RAG index with garbage NN candidates.

    The upsert (rather than insert-only) makes re-promotion of an
    already-promoted signature (shouldn't happen — callers gate on
    ``promoted_at``) or a concurrent/partial rerun idempotent regardless.
    """
    if candidate["tool"] not in dispatchable_tools:
        _log.info("skipping unknown tool %r for sig %s", candidate["tool"], candidate["signature_hash"])
        return False

    content = candidate["last_question"]
    chunk_id = f"cache_{candidate['signature_hash']}"
    vec = embedder.embed(content, mode="passage")
    new_hash = _content_hash(content)

    existing = await conn.fetchrow(
        "SELECT content_hash FROM rag_chunks WHERE agency_id=$1 AND chunk_id=$2",
        agency_id,
        chunk_id,
    )
    if existing is None:
        await conn.execute(
            "INSERT INTO rag_chunks (chunk_id, agency_id, content, embedding, content_hash) "
            "VALUES ($1, $2, $3, $4::vector, $5)",
            chunk_id,
            agency_id,
            content,
            _format_vec(vec),
            new_hash,
        )
    elif existing["content_hash"] != new_hash:
        await conn.execute(
            "UPDATE rag_chunks SET content=$3, embedding=$4::vector, content_hash=$5, embedded_at=now() "
            "WHERE agency_id=$1 AND chunk_id=$2",
            agency_id,
            chunk_id,
            content,
            _format_vec(vec),
            new_hash,
        )

    await intent_cache.mark_promoted(conn, candidate["signature_hash"], agency_id)
    _log.info(
        "promoted %s → %s(%s)",
        candidate["signature_hash"],
        candidate["tool"],
        json.dumps(candidate["args"], ensure_ascii=False),
    )
    return True


async def promote(
    conn: asyncpg.Connection,
    agency_id: int,
    embedder: SupportsEmbed,
    *,
    hit_threshold: int = 5,
    quiet_days: int = 7,
) -> int:
    """Promote every eligible cache row for ``agency_id``: >=hit_threshold
    hits, no ``edited`` action, >=quiet_days old, not yet promoted. Returns
    the number promoted."""
    from pipeline.query.tools import _HANDLERS as _DISPATCHABLE_TOOLS

    candidates = await intent_cache.promotion_candidates(
        conn, agency_id, hit_threshold=hit_threshold, quiet_days=quiet_days
    )
    promoted = 0
    for c in candidates:
        if await _promote_one(conn, embedder, c, agency_id, dispatchable_tools=_DISPATCHABLE_TOOLS):
            promoted += 1
    return promoted


async def promote_signature(
    conn: asyncpg.Connection,
    signature_hash: str,
    agency_id: int,
    embedder: SupportsEmbed,
) -> bool:
    """Promote one specific cache row on admin request.

    Bypasses the ``hit_threshold``/``quiet_days`` eligibility gate the
    scheduled batch job (:func:`promote`) applies — an admin explicitly
    choosing to promote a query overrides those heuristics. Still refuses an
    already-promoted row (idempotent, matching :func:`promote`'s behaviour)
    and an undispatchable tool.

    Returns ``False`` when there's no cache row for this signature, it's
    already promoted, or its tool isn't dispatchable; ``True`` on a
    successful promotion.
    """
    from pipeline.query.tools import _HANDLERS as _DISPATCHABLE_TOOLS

    candidate = await intent_cache.lookup(conn, signature_hash, agency_id)
    if candidate is None or candidate["promoted_at"] is not None:
        return False
    return await _promote_one(conn, embedder, candidate, agency_id, dispatchable_tools=_DISPATCHABLE_TOOLS)
