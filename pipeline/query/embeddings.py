"""Sentence-transformer embedding wrapper for the embedding router.

Loads ``intfloat/multilingual-e5-small`` (384-dim, multilingual) at API
startup. Used by:

* :mod:`pipeline.query.router` — Stage 2 embedding-nearest-neighbor lookup
* :mod:`pipeline.query.rag_index` — index build (embeds golden-set Qs)

Both paths are tolerant of a failed model load: if ``Embedder.available``
is False, callers must fall through to the LLM-only path. API startup never
aborts on embedding failure.
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
        # e5's query/passage asymmetry is expressed by these literal prefixes
        # and nothing else. They must never be combined with
        # sentence-transformers' own ``prompt_name=`` / ``encode_query()``,
        # which prepend a prompt of their own: the doubled prefix embeds as
        # different text than every vector already in the index.
        prefix = "query: " if mode == "query" else "passage: "
        vec = self._model.encode(prefix + truncated, normalize_embeddings=True)
        return [float(x) for x in vec]


def _library_version() -> str:
    """Installed ``sentence-transformers`` version, or ``"absent"``.

    Read from distribution metadata rather than ``sentence_transformers.
    __version__`` so stamping and the reader's version filter cost nothing
    when the (multi-GB) library is not already imported. The two values are
    the same string.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("sentence-transformers")
    except PackageNotFoundError:
        return "absent"


def embedding_version(model_id: str | None = None) -> str:
    """Identity of the embedder that produced a vector: ``"<model>@<library>"``.

    Vectors from two different models — or from two library versions whose
    pooling or normalization differ — share no coordinate system, so cosine
    distance between them is meaningless rather than merely worse. Writers
    stamp this on every row they embed and readers refuse rows carrying a
    different stamp, which turns an invisible relevance collapse into a
    logged "re-index required".
    """
    return f"{model_id or os.environ.get('EMBEDDING_MODEL_ID', _DEFAULT_MODEL)}@{_library_version()}"


_singleton: Embedder | None = None


def get_embedder() -> Embedder:
    global _singleton
    if _singleton is None:
        _singleton = Embedder()
    return _singleton


def reset_embedder_for_tests() -> None:
    global _singleton
    _singleton = None
