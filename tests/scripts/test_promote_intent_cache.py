"""Integration test for the cache → rag_chunks promotion job (transit_test only).

Relies on conftest.py's ``apply_schema`` session fixture (auto-migrates
transit_test) and the DATABASE_URL redirect to ``transit_test``.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from scripts.promote_intent_cache import promote

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_agency(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("DELETE FROM rag_chunks WHERE chunk_id LIKE 'cache_%'")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("DELETE FROM rag_chunks WHERE chunk_id LIKE 'cache_%'")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


async def test_promote_inserts_eligible_and_marks_promoted(conn_with_agency):
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        # Eligible: 5 hits, 8 days old, no edits
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{"metric":"avg_delay"}', 0.9, 5,
                       '遅延が大きい路線TOP10', $2, now() - INTERVAL '8 days', now())""",
            "aaaaaaaaaaaaaaaa",
            agency_id,
        )
        # Ineligible: only 4 hits
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 4, '少し違う質問', $2,
                       now() - INTERVAL '8 days', now())""",
            "bbbbbbbbbbbbbbbb",
            agency_id,
        )

    n = await promote(agency_id, hit_threshold=5, quiet_days=7)
    assert n == 1

    async with pool.acquire() as c:
        promoted_at = await c.fetchval(
            "SELECT promoted_at FROM ask_intent_cache WHERE signature_hash=$1",
            "aaaaaaaaaaaaaaaa",
        )
        assert promoted_at is not None

        # Ineligible row must NOT be promoted
        not_promoted = await c.fetchval(
            "SELECT promoted_at FROM ask_intent_cache WHERE signature_hash=$1",
            "bbbbbbbbbbbbbbbb",
        )
        assert not_promoted is None

        # Verify a rag_chunks row was created with the correct chunk_id and content
        chunks = await c.fetch(
            "SELECT content FROM rag_chunks WHERE content = '遅延が大きい路線TOP10' AND agency_id = $1",
            agency_id,
        )
        assert len(chunks) == 1

        chunk_row = await c.fetchrow(
            "SELECT chunk_id, content FROM rag_chunks WHERE chunk_id = 'cache_aaaaaaaaaaaaaaaa' AND agency_id = $1",
            agency_id,
        )
        assert chunk_row is not None
        assert chunk_row["content"] == "遅延が大きい路線TOP10"


async def test_promote_idempotent_does_not_re_promote(conn_with_agency):
    """Running promote twice does not produce duplicate rag_chunks."""
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{"metric":"avg_delay"}', 0.9, 6,
                       '別の質問', $2, now() - INTERVAL '10 days', now())""",
            "cccccccccccccccc",
            agency_id,
        )

    n1 = await promote(agency_id)
    n2 = await promote(agency_id)
    assert n1 == 1
    assert n2 == 0  # already promoted — promotion_candidates returns empty

    async with pool.acquire() as c:
        cnt = await c.fetchval(
            "SELECT count(*) FROM rag_chunks WHERE content = '別の質問' AND agency_id = $1",
            agency_id,
        )
    assert cnt == 1


async def test_promote_empty_candidates_returns_zero(conn_with_agency):
    """No eligible rows → returns 0 and touches nothing."""
    pool, agency_id = conn_with_agency
    # Insert a row that is too recent to qualify
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 10,
                       '新しい質問', $2, now() - INTERVAL '3 days', now())""",
            "dddddddddddddddd",
            agency_id,
        )

    n = await promote(agency_id, hit_threshold=5, quiet_days=7)
    assert n == 0

    async with pool.acquire() as c:
        cnt = await c.fetchval(
            "SELECT count(*) FROM rag_chunks WHERE agency_id = $1",
            agency_id,
        )
    assert cnt == 0


async def test_promote_skips_edited_action(conn_with_agency):
    """A row with last_user_action='edited' must not be promoted even if hits >= threshold."""
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, last_user_action,
               created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 8,
                       '修正された質問', $2, 'edited',
                       now() - INTERVAL '9 days', now())""",
            "eeeeeeeeeeeeeeee",
            agency_id,
        )

    n = await promote(agency_id)
    assert n == 0

    async with pool.acquire() as c:
        cnt = await c.fetchval(
            "SELECT count(*) FROM rag_chunks WHERE agency_id = $1",
            agency_id,
        )
    assert cnt == 0
