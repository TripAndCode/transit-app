"""ask_intent_cache async DAL tests (transit_test via conftest auto-redirect)."""

from __future__ import annotations

import pytest

from pipeline.query.intent import IntentSignature
from pipeline.query.intent_cache import (
    lookup,
    update_user_action,
    upsert,
)
from tests.conftest import _test_pool


@pytest.fixture
async def conn_with_agency(apply_schema):
    """Single asyncpg connection + agency_id 1; cleans cache table between tests."""
    pool = await _test_pool()
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
        assert row is not None
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
        assert row is not None
        assert row["last_user_action"] == "edited"


@pytest.mark.asyncio
async def test_update_user_action_rejects_bad_action(conn_with_agency):
    pool, agency_id = conn_with_agency
    async with pool.acquire() as c:
        with pytest.raises(ValueError):
            await update_user_action(c, "deadbeefcafe1234", agency_id, "garbage")


@pytest.mark.asyncio
async def test_upsert_refreshes_the_question_even_on_a_promoted_row(conn_with_agency):
    """A row with `promoted_at` set holds no question of its own: a later
    asker's wording replaces it, and the hit counts."""
    pool, agency_id = conn_with_agency
    sig_hash = "8888888888888888"
    async with pool.acquire() as c:
        await upsert(c, sig_hash, _sig(), {"metric": "avg_delay"}, agency_id, question="Promoted Q")
        await c.execute("UPDATE ask_intent_cache SET promoted_at = now() WHERE signature_hash = $1", sig_hash)
        await upsert(c, sig_hash, _sig(), {"metric": "avg_delay"}, agency_id, question="A later Q")
        row = await lookup(c, sig_hash, agency_id)
        assert row is not None
        assert row["last_question"] == "A later Q"
        assert row["hit_count"] == 2
