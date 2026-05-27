"""Sentence-transformer embedding wrapper for the Phase 2 router.

Loads ``intfloat/multilingual-e5-small`` (384-dim, multilingual) at API
startup. Used by:

* :mod:`pipeline.query.router` — Stage 2 embedding-nearest-neighbor lookup
* :mod:`pipeline.query.rag_index` — index build (embeds golden-set Qs)

Both paths are tolerant of a failed model load: if ``Embedder.available``
is False, callers must fall through to the LLM-only path (Phase 1
behavior). API startup never aborts on embedding failure.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "intfloat/multilingual-e5-small"
_DIM = 384
_MAX_CHARS = 512


class Embedder:
    """Thread-safe wrapper around a sentence-transformers model.

    Constructor blocks on model load (~2-3s warm, ~30s cold). On failure,
    sets ``available=False`` and ``embed()`` raises; the caller decides
    how to degrade.
    """

    available: bool
    dim: int

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or os.environ.get("EMBEDDING_MODEL_ID", _DEFAULT_MODEL)
        self.dim = _DIM
        self._model = None
        self.available = False
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_id)
            self.available = True
            _log.info("Embedder ready: model=%s dim=%d", self.model_id, self.dim)
        except Exception as exc:
            _log.error(
                "Embedder unavailable (model=%s): %s — Stage 2 router will fall through to LLM",
                self.model_id,
                exc.__class__.__name__,
            )

    def embed(self, text: str, *, mode: Literal["query", "passage"]) -> list[float]:
        if not self.available or self._model is None:
            raise RuntimeError(f"Embedder unavailable (model={self.model_id})")
        truncated = (text or "")[:_MAX_CHARS]
        prefix = "query: " if mode == "query" else "passage: "
        vec = self._model.encode(prefix + truncated, normalize_embeddings=True)
        return [float(x) for x in vec]


_singleton: Embedder | None = None


def get_embedder() -> Embedder:
    global _singleton
    if _singleton is None:
        _singleton = Embedder()
    return _singleton


def reset_embedder_for_tests() -> None:
    global _singleton
    _singleton = None
