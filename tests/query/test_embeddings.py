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
