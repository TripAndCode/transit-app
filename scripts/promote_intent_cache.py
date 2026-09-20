"""Promote recurring ask_intent_cache rows into rag_chunks.

A row is promoted when it has been observed >=hit_threshold times with no
``edited`` user_action, >=quiet_days since first observed, and hasn't been
promoted yet.  After insertion the cache row's promoted_at is stamped so it
isn't re-promoted.

This is a thin CLI wrapper: the eligibility scan + rag_chunks upsert logic
lives in ``pipeline.query.intent_promotion`` so the admin "promote to intent
cache" action (``api/routers/admin_ask.py``, single-row, bypasses the
hit_threshold/quiet_days gate) can reuse it without duplicating the
embedding/upsert code.

Usage::

    poetry run python scripts/promote_intent_cache.py --agency-id 1
    poetry run python scripts/promote_intent_cache.py --agency-id 1 --hit-threshold 5 --quiet-days 7
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

import asyncpg

from pipeline.query.embeddings import get_embedder
from pipeline.query.intent_promotion import promote as _promote_core

_log = logging.getLogger(__name__)


async def promote(agency_id: int, hit_threshold: int = 5, quiet_days: int = 7) -> int:
    """Promote eligible cache rows into rag_chunks.  Returns number promoted."""
    embedder = get_embedder()
    if not embedder.available:
        raise RuntimeError("Embedder unavailable — cannot promote intent cache rows")

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        return await _promote_core(conn, agency_id, embedder, hit_threshold=hit_threshold, quiet_days=quiet_days)
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
