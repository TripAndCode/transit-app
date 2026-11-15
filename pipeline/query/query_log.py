"""Anonymized per-question analytics log for the Ask tab.

One flat row per /ask call: question + routing metadata, NO identity.
``log_query`` never raises — a logging failure must never affect the
user's answer. See ``db/migrations/0013_ask_query_log``.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 1000


async def log_query(
    conn,
    agency_id: int,
    question: str,
    router_stage: str,
    tool: str | None,
    success: bool,
    *,
    signature_hash: str | None = None,
    cache_outcome: str | None = None,
    numeric_guard_triggered: bool | None = None,
    latency_ms: int | None = None,
) -> None:
    """Insert one anonymized query-log row. Swallows all errors.

    ``signature_hash`` and ``cache_outcome`` are populated only when the
    ``ask_intent_cache_enabled`` flag is on and the question reached the
    LLM stage; they are NULL otherwise.

    ``numeric_guard_triggered`` is the column for a post-generation
    numeric-guard verdict. Only a conversation follow-up produces one (see
    ``pipeline.query.followup.answer_followup``): a dispatched tool's answer is
    rendered from grounded data, and the Stage-3 plain-text reply has nothing
    to trace a number back to (see ``pipeline.query.chat._text_reply``), so
    every other row records NULL ("no verdict"), which must stay distinct from
    FALSE ("ran, answer was clean").

    ``latency_ms`` is measured only for notebook card dispatches (the
    dispatch itself) and follow-ups (the model call); other rows leave it NULL.

    The write is its own statement. A caller inside a transaction must log
    after it ends: a failed INSERT aborts the transaction it runs in, and
    swallowing the exception here cannot undo that.
    """
    try:
        await conn.execute(
            "INSERT INTO ask_query_log "
            "(agency_id, question, router_stage, tool, success, signature_hash, cache_outcome, "
            "numeric_guard_triggered, latency_ms) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            agency_id,
            (question or "")[:_MAX_QUESTION_CHARS],
            router_stage,
            tool,
            success,
            signature_hash,
            cache_outcome,
            numeric_guard_triggered,
            latency_ms,
        )
    except Exception as exc:
        _log.warning("ask_query_log insert failed (%s) — ignored", exc.__class__.__name__)
