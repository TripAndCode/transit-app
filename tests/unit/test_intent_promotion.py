"""Pure-logic tests for pipeline.query.intent_promotion's rag_chunks upsert
and single-signature promotion guard, faking asyncpg + the embedder so no DB
is needed (CLAUDE.md's tests/unit convention). DB-backed coverage of the
full promotion job (eligibility SQL, idempotency across real inserts) stays
in tests/scripts/test_promote_intent_cache.py and tests/query/test_intent_cache.py.
"""

from __future__ import annotations

import pytest

from pipeline.query import intent_promotion


class _FakeEmbedder:
    available = True

    def embed(self, text: str, *, mode: str) -> list[float]:
        return [0.1, 0.2]


class _FakeConn:
    """Fakes only the rag_chunks statements intent_promotion issues
    directly. ask_intent_cache access goes through pipeline.query.intent_cache,
    which tests monkeypatch instead of faking its SQL here."""

    def __init__(self, existing_hash: str | None = None, existing_version: str | None = None):
        self.existing_hash = existing_hash
        self.existing_version = existing_version
        self.insert_calls: list[tuple] = []
        self.update_calls: list[tuple] = []

    async def fetchrow(self, sql, *args):
        assert "rag_chunks" in sql
        if self.existing_hash is None:
            return None
        return {"content_hash": self.existing_hash, "embedding_version": self.existing_version}

    async def execute(self, sql, *args):
        stripped = sql.strip()
        if stripped.startswith("INSERT INTO rag_chunks"):
            self.insert_calls.append(args)
        elif stripped.startswith("UPDATE rag_chunks"):
            self.update_calls.append(args)
        else:
            raise AssertionError(f"unexpected execute: {sql}")


@pytest.fixture
def mark_promoted_calls(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_mark_promoted(conn, signature_hash, agency_id, embedding_version=None):
        calls.append((signature_hash, agency_id))

    monkeypatch.setattr(intent_promotion.intent_cache, "mark_promoted", fake_mark_promoted)
    return calls


def _candidate(tool="top_n", sig="aaaa", promoted_at=None):
    return {
        "signature_hash": sig,
        "tool": tool,
        "args": {},
        "last_question": "Q?",
        "promoted_at": promoted_at,
    }


async def test_promote_one_inserts_new_chunk_and_marks_promoted(mark_promoted_calls):
    conn = _FakeConn(existing_hash=None)
    ok = await intent_promotion._promote_one(conn, _FakeEmbedder(), _candidate(), 1, dispatchable_tools={"top_n"})
    assert ok is True
    assert len(conn.insert_calls) == 1
    assert conn.update_calls == []
    assert mark_promoted_calls == [("aaaa", 1)]


async def test_promote_one_updates_when_content_hash_changed(mark_promoted_calls):
    conn = _FakeConn(existing_hash="stale-hash")
    ok = await intent_promotion._promote_one(conn, _FakeEmbedder(), _candidate(), 1, dispatchable_tools={"top_n"})
    assert ok is True
    assert conn.insert_calls == []
    assert len(conn.update_calls) == 1


async def test_promote_one_skips_undispatchable_tool(mark_promoted_calls):
    conn = _FakeConn()
    ok = await intent_promotion._promote_one(
        conn, _FakeEmbedder(), _candidate(tool="none"), 1, dispatchable_tools={"top_n"}
    )
    assert ok is False
    assert conn.insert_calls == []
    assert mark_promoted_calls == []


async def test_promote_signature_false_when_no_cache_row(monkeypatch):
    async def fake_lookup(conn, sig, agency_id):
        return None

    monkeypatch.setattr(intent_promotion.intent_cache, "lookup", fake_lookup)
    ok = await intent_promotion.promote_signature(_FakeConn(), "aaaa", 1, _FakeEmbedder())
    assert ok is False


async def test_promote_signature_false_when_already_promoted(monkeypatch):
    async def fake_lookup(conn, sig, agency_id):
        return _candidate(promoted_at="2026-01-01T00:00:00Z")

    monkeypatch.setattr(intent_promotion.intent_cache, "lookup", fake_lookup)
    ok = await intent_promotion.promote_signature(_FakeConn(), "aaaa", 1, _FakeEmbedder())
    assert ok is False


async def test_promote_signature_false_for_undispatchable_tool(monkeypatch):
    async def fake_lookup(conn, sig, agency_id):
        return _candidate(tool="not_a_real_tool")

    monkeypatch.setattr(intent_promotion.intent_cache, "lookup", fake_lookup)
    ok = await intent_promotion.promote_signature(_FakeConn(), "aaaa", 1, _FakeEmbedder())
    assert ok is False


async def test_promote_signature_promotes_eligible_row(monkeypatch, mark_promoted_calls):
    async def fake_lookup(conn, sig, agency_id):
        return _candidate()

    monkeypatch.setattr(intent_promotion.intent_cache, "lookup", fake_lookup)
    conn = _FakeConn()
    ok = await intent_promotion.promote_signature(conn, "aaaa", 1, _FakeEmbedder())
    assert ok is True
    assert mark_promoted_calls == [("aaaa", 1)]
