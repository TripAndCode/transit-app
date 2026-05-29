"""Promote recurring ask_intent_cache rows into rag_chunks.

A row is promoted when it has been observed >=hit_threshold times with no
``edited`` user_action, >=quiet_days since first observed, and hasn't been
promoted yet.  After insertion the cache row's promoted_at is stamped so it
isn't re-promoted.

The inserted chunk reuses the same e5 ``passage:`` prefix as
``build_rag_index`` so the Stage-2 embedding nearest-neighbor query in
``rag_index.nearest()`` can find promoted questions without modification.

chunk_id format: ``cache_<signature_hash>`` (16-hex chars) — unique per
(agency, canonical intent) and clearly identifies the source.

Usage::

    poetry run python scripts/promote_intent_cache.py --agency-id 1
    poetry run python scripts/promote_intent_cache.py --agency-id 1 --hit-threshold 5 --quiet-days 7
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os

import asyncpg

from pipeline.query import intent_cache
from pipeline.query.embeddings import get_embedder
from pipeline.query.rag_index import _format_vec

_log = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def promote(agency_id: int, hit_threshold: int = 5, quiet_days: int = 7) -> int:
    """Promote eligible cache rows into rag_chunks.  Returns number promoted."""
    embedder = get_embedder()
    if not embedder.available:
        raise RuntimeError("Embedder unavailable — cannot promote intent cache rows")

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        candidates = await intent_cache.promotion_candidates(
            conn,
            agency_id,
            hit_threshold=hit_threshold,
            quiet_days=quiet_days,
        )
        promoted = 0
        for c in candidates:
            content = c["last_question"]
            chunk_id = f"cache_{c['signature_hash']}"

            # Embed using the same convention as build_rag_index (passage: prefix).
            vec = embedder.embed(content, mode="passage")
            new_hash = _content_hash(content)

            # Upsert: if a promoted chunk already exists (e.g. question text
            # changed) update it; otherwise insert fresh.  In practice the
            # promoted_at guard on promotion_candidates means we'll never
            # re-visit an already-promoted row, but the upsert makes the job
            # fully idempotent if called concurrently or after a partial run.
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

            await intent_cache.mark_promoted(conn, c["signature_hash"], agency_id)
            promoted += 1
            _log.info(
                "promoted %s → %s(%s)",
                c["signature_hash"],
                c["tool"],
                json.dumps(c["args"], ensure_ascii=False),
            )
        return promoted
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Promote recurring ask_intent_cache rows into rag_chunks.")
    parser.add_argument("--agency-id", type=int, required=True)
    parser.add_argument("--hit-threshold", type=int, default=5)
    parser.add_argument("--quiet-days", type=int, default=7)
    args = parser.parse_args()
    n = asyncio.run(promote(args.agency_id, args.hit_threshold, args.quiet_days))
    print(f"promoted {n} signature(s)")


if __name__ == "__main__":
    main()
