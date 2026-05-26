"""``rag_chunks`` reader for the Phase 2 router.

Stores one row per indexed golden-set question. Tool + args for each
chunk live in ``tests/ask_eval/golden_set.jsonl`` (the canonical source);
``rag_chunks`` is purely the embedding index.

Build is idempotent via ``content_hash`` — rerunning
``gtfs_pipeline.py build_rag_index`` only re-embeds rows whose canonical
question text changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    """One nearest-neighbor row, joined to its golden_set entry."""

    chunk_id: str
    content: str
    tool: str
    args: dict
    distance: float


def _format_vec(vec: list[float]) -> str:
    """pgvector text-cast format: '[v1,v2,...,vN]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def nearest(conn, agency_id: int, qvec: list[float], k: int = 3) -> list[Match]:
    """Top-k chunks by cosine distance, scoped to ``agency_id``.

    Returns ``[]`` when the agency has no indexed chunks. Each returned
    :class:`Match` has empty ``tool``/``args`` — the caller joins to the
    golden-set dict to recover them.
    """
    rows = await conn.fetch(
        "SELECT chunk_id, content, (embedding <=> $1::vector) AS distance "
        "FROM rag_chunks "
        "WHERE agency_id = $2 "
        "ORDER BY embedding <=> $1::vector "
        "LIMIT $3",
        _format_vec(qvec),
        agency_id,
        k,
    )
    return [
        Match(chunk_id=r["chunk_id"], content=r["content"], tool="", args={}, distance=float(r["distance"]))
        for r in rows
    ]
