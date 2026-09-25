"""Embedding-version stamping and drift guard, with no DB and no model load.

Every assertion here is about the SQL text and arguments the writers/readers
issue, so the embedder and the connection are both fakes (CLAUDE.md's
tests/unit convention). DB-backed coverage of the same statements lives in
tests/query/test_rag_index.py and tests/scripts/test_promote_intent_cache.py.
"""

from __future__ import annotations

import json
import logging

import pytest

from pipeline.query import embeddings, intent_cache, intent_promotion, rag_index


class _FakeEmbedder:
    available = True
    model_id = "fake/model"

    def embed(self, text: str, *, mode: str) -> list[float]:
        return [0.1, 0.2]


class _RecordingConn:
    """Records every statement a writer/reader issues.

    ``existing`` is the row returned for the "does this chunk already exist"
    probe; ``drift_row`` the row returned for the drift probe.
    """

    def __init__(self, existing: dict | None = None, drift_row: dict | None = None):
        self.existing = existing
        self.drift_row = drift_row
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []
        self.fetchrows: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.fetchrows.append((sql, args))
        if "embedding_version IS NOT NULL" in sql:
            return self.drift_row
        return self.existing

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    def statements(self, prefix: str) -> list[tuple[str, tuple]]:
        return [(s, a) for s, a in self.executed if s.strip().startswith(prefix)]


@pytest.fixture(autouse=True)
def _reset_drift_state():
    rag_index.reset_version_drift_state_for_tests()
    yield
    rag_index.reset_version_drift_state_for_tests()


def test_embedding_version_joins_model_and_library(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "intfloat/multilingual-e5-small")
    version = embeddings.embedding_version()
    model, _, library = version.partition("@")
    assert model == "intfloat/multilingual-e5-small"
    assert library and library != "@"


def test_embedding_version_uses_explicit_model_id(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "env/model")
    assert embeddings.embedding_version("explicit/model").startswith("explicit/model@")


async def test_build_index_stamps_version(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")
    golden = tmp_path / "golden_set.jsonl"
    golden.write_text(json.dumps({"id": "q1", "question": "Q?"}) + "\n")
    conn = _RecordingConn(existing=None)

    counts = await rag_index.build_index(conn, 1, golden, embedder=_FakeEmbedder())

    assert counts["inserted"] == 1
    sql, args = conn.statements("INSERT INTO rag_chunks")[0]
    assert "embedding_version" in sql
    assert "m@1" in args


async def test_build_index_reembeds_a_row_stamped_with_another_version(tmp_path, monkeypatch):
    """Content unchanged but the stamp is stale: the row must be rebuilt, not
    skipped — otherwise the reader's version filter hides it forever."""
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@2")
    golden = tmp_path / "golden_set.jsonl"
    golden.write_text(json.dumps({"id": "q1", "question": "Q?"}) + "\n")
    conn = _RecordingConn(existing={"content_hash": rag_index._content_hash("Q?"), "embedding_version": "m@1"})

    counts = await rag_index.build_index(conn, 1, golden, embedder=_FakeEmbedder())

    assert counts == {"inserted": 0, "updated": 1, "skipped": 0}
    sql, args = conn.statements("UPDATE rag_chunks")[0]
    assert "embedding_version" in sql
    assert "m@2" in args


async def test_build_index_still_skips_an_up_to_date_row(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")
    golden = tmp_path / "golden_set.jsonl"
    golden.write_text(json.dumps({"id": "q1", "question": "Q?"}) + "\n")
    conn = _RecordingConn(existing={"content_hash": rag_index._content_hash("Q?"), "embedding_version": "m@1"})

    counts = await rag_index.build_index(conn, 1, golden, embedder=_FakeEmbedder())

    assert counts == {"inserted": 0, "updated": 0, "skipped": 1}
    assert conn.executed == []


async def test_promote_one_stamps_version(monkeypatch):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")

    marked: list[tuple] = []

    async def fake_mark_promoted(conn, signature_hash, agency_id, embedding_version=None):
        marked.append((signature_hash, agency_id, embedding_version))

    monkeypatch.setattr(intent_promotion.intent_cache, "mark_promoted", fake_mark_promoted)
    conn = _RecordingConn(existing=None)
    candidate = {"signature_hash": "aaaa", "tool": "top_n", "args": {}, "last_question": "Q?"}

    ok = await intent_promotion._promote_one(conn, _FakeEmbedder(), candidate, 1, dispatchable_tools={"top_n"})

    assert ok is True
    sql, args = conn.statements("INSERT INTO rag_chunks")[0]
    assert "embedding_version" in sql
    assert "m@1" in args
    assert marked == [("aaaa", 1, "m@1")]


async def test_promote_one_reembeds_on_version_drift(monkeypatch):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@2")

    async def fake_mark_promoted(conn, signature_hash, agency_id, embedding_version=None):
        return None

    monkeypatch.setattr(intent_promotion.intent_cache, "mark_promoted", fake_mark_promoted)
    conn = _RecordingConn(existing={"content_hash": intent_promotion._content_hash("Q?"), "embedding_version": "m@1"})
    candidate = {"signature_hash": "aaaa", "tool": "top_n", "args": {}, "last_question": "Q?"}

    await intent_promotion._promote_one(conn, _FakeEmbedder(), candidate, 1, dispatchable_tools={"top_n"})

    sql, args = conn.statements("UPDATE rag_chunks")[0]
    assert "embedding_version" in sql
    assert "m@2" in args


async def test_mark_promoted_stamps_version():
    conn = _RecordingConn()
    await intent_cache.mark_promoted(conn, "aaaa", 1, embedding_version="m@1")
    sql, args = conn.executed[0]
    assert "embedding_version" in sql
    assert "m@1" in args


async def test_nearest_filters_to_current_or_unstamped_rows(monkeypatch):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")
    conn = _RecordingConn()

    await rag_index.nearest(conn, 1, [0.1, 0.2], k=3)

    sql, args = conn.fetched[0]
    assert "embedding_version IS NULL" in sql
    assert "embedding_version = " in sql
    assert "m@1" in args


async def test_nearest_warns_once_when_other_versions_exist(monkeypatch, caplog):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@2")
    conn = _RecordingConn(drift_row={"?column?": 1})

    with caplog.at_level(logging.WARNING, logger="pipeline.query.rag_index"):
        await rag_index.nearest(conn, 1, [0.1, 0.2])
        await rag_index.nearest(conn, 1, [0.1, 0.2])

    drift_warnings = [r for r in caplog.records if "re-index" in r.getMessage()]
    assert len(drift_warnings) == 1
    assert "m@2" in drift_warnings[0].getMessage()
    assert len([s for s, _ in conn.fetchrows if "embedding_version IS NOT NULL" in s]) == 1


async def test_nearest_is_quiet_when_no_other_versions_exist(monkeypatch, caplog):
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")
    conn = _RecordingConn(drift_row=None)

    with caplog.at_level(logging.WARNING, logger="pipeline.query.rag_index"):
        await rag_index.nearest(conn, 1, [0.1, 0.2])

    assert [r for r in caplog.records if "re-index" in r.getMessage()] == []


async def test_nearest_survives_a_failing_drift_probe(monkeypatch):
    """The guard is advisory: a probe that errors must not break retrieval."""
    monkeypatch.setattr(embeddings, "embedding_version", lambda model_id=None: "m@1")

    class _ProbeFails(_RecordingConn):
        async def fetchrow(self, sql, *args):
            raise RuntimeError("no such column")

    conn = _ProbeFails()
    assert await rag_index.nearest(conn, 1, [0.1, 0.2]) == []
