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


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def build_index(conn, agency_id: int, golden_set_path: Path, embedder=None) -> dict:
    """Embed every (id, question) line from the golden set + upsert into rag_chunks.

    Returns counts: ``{"inserted": N, "updated": N, "skipped": N}``. Skip
    means a row already exists with the same content_hash. Lines missing
    ``id`` or ``question`` are ignored with a logged warning.
    """
    if embedder is None:
        from pipeline.query.embeddings import get_embedder

        embedder = get_embedder()
    if not embedder.available:
        raise RuntimeError("Embedder unavailable — cannot build index")

    inserted = updated = skipped = 0
    with golden_set_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            chunk_id = entry.get("id")
            question = entry.get("question")
            if not chunk_id or not question:
                _log.warning("golden_set line missing id/question: %r — skipped", line[:80])
                continue
            new_hash = _content_hash(question)
            existing = await conn.fetchrow(
                "SELECT content_hash FROM rag_chunks WHERE agency_id=$1 AND chunk_id=$2",
                agency_id,
                chunk_id,
            )
            if existing is not None and existing["content_hash"] == new_hash:
                skipped += 1
                continue
            vec = embedder.embed(question, mode="passage")
            if existing is None:
                await conn.execute(
                    "INSERT INTO rag_chunks (chunk_id, agency_id, content, embedding, content_hash) "
                    "VALUES ($1, $2, $3, $4::vector, $5)",
                    chunk_id,
                    agency_id,
                    question,
                    _format_vec(vec),
                    new_hash,
                )
                inserted += 1
            else:
                await conn.execute(
                    "UPDATE rag_chunks SET content=$3, embedding=$4::vector, content_hash=$5, embedded_at=now() "
                    "WHERE agency_id=$1 AND chunk_id=$2",
                    agency_id,
                    chunk_id,
                    question,
                    _format_vec(vec),
                    new_hash,
                )
                updated += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped}
