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
) -> None:
    """Insert one anonymized query-log row. Swallows all errors.

    ``signature_hash`` and ``cache_outcome`` are populated only when the
    intent-cache feature flag is enabled (Phase ②+); they are NULL when
    the flag is off (Phase ①-compatible).
    """
    try:
        await conn.execute(
            "INSERT INTO ask_query_log "
            "(agency_id, question, router_stage, tool, success, signature_hash, cache_outcome) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            agency_id,
            (question or "")[:_MAX_QUESTION_CHARS],
            router_stage,
            tool,
            success,
            signature_hash,
            cache_outcome,
        )
    except Exception as exc:
        _log.warning("ask_query_log insert failed (%s) — ignored", exc.__class__.__name__)
