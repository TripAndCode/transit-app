"""ask_intent_cache async DAL tests (transit_test via conftest auto-redirect)."""

from __future__ import annotations

import os

import asyncpg
import pytest

from pipeline.query.intent import IntentSignature
from pipeline.query.intent_cache import (
    lookup,
    mark_promoted,
    promotion_candidates,
    update_user_action,
    upsert,
)

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_agency(apply_schema):
    """Single asyncpg connection + agency_id 1; cleans cache table between tests."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")  # safe: transit_test only
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') "
            "ON CONFLICT DO NOTHING RETURNING agency_id"
        )
        if row is None:
            row = await c.fetchrow("SELECT agency_id FROM agencies LIMIT 1")
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _sig(tool="top_n", args=None, conf=0.9):
    return IntentSignature(tool=tool, args=args or {"metric": "avg_delay"}, confidence=conf)


@pytest.mark.asyncio
async def test_lookup_returns_none_for_unknown_hash(conn_with_agency):
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        row = await lookup(c, "0000000000000000", agency_id)
    assert row is None


@pytest.mark.asyncio
async def test_upsert_inserts_then_increments_hit_count(conn_with_agency):
    pool, agency_id = conn_with_agency
    sig = _sig()
    sig_hash = "abcdef0123456789"
    canonical = {"metric": "avg_delay"}
    async with pool.acquire() as c:
        await upsert(c, sig_hash, sig, canonical, agency_id, question="Q1")
        row = await lookup(c, sig_hash, agency_id)
        assert row is not None
        assert row["hit_count"] == 1
        assert row["last_question"] == "Q1"

        await upsert(c, sig_hash, sig, canonical, agency_id, question="Q1 paraphrased")
        row = await lookup(c, sig_hash, agency_id)
        assert row["hit_count"] == 2
        assert row["last_question"] == "Q1 paraphrased"


@pytest.mark.asyncio
async def test_update_user_action_writes_edited(conn_with_agency):
    pool, agency_id = conn_with_agency
    sig_hash = "1234567890abcdef"
    async with pool.acquire() as c:
        await upsert(c, sig_hash, _sig(), {"metric": "avg_delay"}, agency_id, question="Q")
        await update_user_action(c, sig_hash, agency_id, "edited")
        row = await lookup(c, sig_hash, agency_id)
        assert row["last_user_action"] == "edited"


@pytest.mark.asyncio
async def test_update_user_action_rejects_bad_action(conn_with_agency):
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        with pytest.raises(ValueError):
            await update_user_action(c, "deadbeefcafe1234", agency_id, "garbage")


@pytest.mark.asyncio
async def test_promotion_candidates_filters_correctly(conn_with_agency):
    """Only rows with hit_count>=threshold, no-edited, >=quiet_days old, not yet promoted."""
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        # Eligible: 5 hits, no action, created 8 days ago, not promoted
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 5, 'Q-eligible', $2,
                       now() - INTERVAL '8 days', now())""",
            "1111111111111111",
            agency_id,
        )
        # Ineligible: only 4 hits
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 4, 'Q-low-hits', $2,
                       now() - INTERVAL '8 days', now())""",
            "2222222222222222",
            agency_id,
        )
        # Ineligible: edited
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, last_user_action, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 5, 'Q-edited', 'edited', $2,
                       now() - INTERVAL '8 days', now())""",
            "3333333333333333",
            agency_id,
        )
        # Ineligible: too new (<7 days)
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 10, 'Q-too-new', $2,
                       now() - INTERVAL '3 days', now())""",
            "4444444444444444",
            agency_id,
        )
        # Ineligible: already promoted
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id, created_at, last_used_at, promoted_at)
               VALUES ($1, 'top_n', '{}', 0.9, 6, 'Q-already-promoted', $2,
                       now() - INTERVAL '10 days', now(), now())""",
            "5555555555555555",
            agency_id,
        )

        rows = await promotion_candidates(c, agency_id)
        sigs = {r["signature_hash"] for r in rows}
        assert sigs == {"1111111111111111"}


@pytest.mark.asyncio
async def test_promotion_candidates_accepts_confirmed_action(conn_with_agency):
    """last_user_action='confirmed' must still be eligible for promotion."""
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, last_user_action, agency_id, created_at, last_used_at)
               VALUES ($1, 'top_n', '{}', 0.9, 5, 'Q-confirmed', 'confirmed', $2,
                       now() - INTERVAL '8 days', now())""",
            "6666666666666666",
            agency_id,
        )
        rows = await promotion_candidates(c, agency_id)
        assert {r["signature_hash"] for r in rows} == {"6666666666666666"}


@pytest.mark.asyncio
async def test_mark_promoted_sets_timestamp(conn_with_agency):
    pool, agency_id = conn_with_agency
    sig_hash = "7777777777777777"
    async with pool.acquire() as c:
        await upsert(c, sig_hash, _sig(), {"metric": "avg_delay"}, agency_id, question="Q")
        await mark_promoted(c, sig_hash, agency_id)
        row = await lookup(c, sig_hash, agency_id)
        assert row["promoted_at"] is not None
