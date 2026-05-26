"""Embedder unit tests. The success-path tests are gated under RUN_SLOW
because they download / load a ~120MB model."""

import os

import pytest

from pipeline.query.embeddings import Embedder, get_embedder, reset_embedder_for_tests


@pytest.fixture(autouse=True)
def _reset_embedder():
    reset_embedder_for_tests()
    yield
    reset_embedder_for_tests()


def test_embedder_unavailable_when_model_id_invalid(monkeypatch):
    """Failure path: bad model id → available=False, no crash."""
    e = Embedder(model_id="nonexistent/this-model-does-not-exist")
    assert e.available is False
    with pytest.raises(RuntimeError):
        e.embed("hello", mode="query")


def test_get_embedder_returns_singleton(monkeypatch):
    """get_embedder() returns the same instance across calls."""
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "nonexistent/never-loads")
    a = get_embedder()
    b = get_embedder()
    assert a is b


RUN_SLOW = os.environ.get("RUN_SLOW") == "1"


@pytest.mark.slow
@pytest.mark.skipif(not RUN_SLOW, reason="RUN_SLOW=1 not set")
def test_embedder_dim_and_cosine_similarity():
    """Live: loads the real model. Asserts dim=384 and that JP+EN
    near-paraphrases produce vectors with cosine similarity > 0.5."""
    e = Embedder()
    assert e.available is True
    assert e.dim == 384

    v_jp = e.embed("どんな路線がデータにあるの？", mode="query")
    v_en = e.embed("what routes are in the data?", mode="query")
    v_passage = e.embed("路線一覧見せて", mode="passage")

    assert len(v_jp) == 384
    assert len(v_en) == 384
    assert len(v_passage) == 384

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))  # vectors are normalize_embeddings=True

    assert cos(v_jp, v_en) > 0.5  # JP/EN paraphrase similarity
    assert cos(v_jp, v_passage) > 0.5  # JP query/passage similarity
