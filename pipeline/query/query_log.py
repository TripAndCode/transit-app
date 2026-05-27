"""Anonymized per-question analytics log for the Ask tab.

One flat row per /ask call: question + routing metadata, NO identity.
``log_query`` never raises — a logging failure must never affect the
user's answer. See ``db/migrations/0013_ask_query_log``.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 1000


async def log_query(conn, agency_id: int, question: str, router_stage: str, tool: str | None, success: bool) -> None:
    """Insert one anonymized query-log row. Swallows all errors."""
    try:
        await conn.execute(
            "INSERT INTO ask_query_log (agency_id, question, router_stage, tool, success) VALUES ($1, $2, $3, $4, $5)",
            agency_id,
            (question or "")[:_MAX_QUESTION_CHARS],
            router_stage,
            tool,
            success,
        )
    except Exception as exc:
        _log.warning("ask_query_log insert failed (%s) — ignored", exc.__class__.__name__)
