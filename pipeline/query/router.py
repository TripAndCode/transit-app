"""Phase 2 pre-LLM router.

Three-stage pipeline orchestrated from :func:`route_question`:

1. **Rules** — :data:`_RULES` regex match → direct dispatch.
2. **Embedding** — nearest golden-Q in ``rag_chunks``; if distance < 0.15
   → direct dispatch using that Q's stored tool/args.
3. **(caller)** — When ``route_question`` returns ``None``,
   :func:`retrieve_examples` provides top-3 nearest as few-shot context
   for the LLM call (Stage 3, in :mod:`pipeline.query.chat`).

The router is additive: any failure (no rule match, embedder unavailable,
empty index) returns ``None`` so the caller falls through to the LLM with
empty examples. Phase 1's behavior continues to work end-to-end.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)

# Distance below which Stage 2 will dispatch directly (cosine distance;
# smaller = closer). The eval target is ≥0.85 cosine similarity, i.e.
# distance ≤ 0.15.
_EMBED_DISPATCH_THRESHOLD = 0.15

# How many golden examples to retrieve for Stage 3 RAG injection.
_RAG_TOP_K = 3


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouterDecision:
    stage: Literal["rules", "embedding"]
    tool: str
    args: dict[str, Any]
    score: float
    matched_pattern: str | None


# Compile regexes ONCE at import time. First match wins (priority = order).
_RULES: list[Rule] = [
    # ---- describe_data meta-tool fan-out ----
    Rule(
        name="meta-routes",
        pattern=re.compile(r"(どんな.*?(路線|系統))|((路線|系統).*?(一覧|リスト))|(何.*?路線.*?(ある|登録))"),
        tool="describe_data",
        args={"kind": "routes"},
    ),
    Rule(
        name="meta-date-range",
        pattern=re.compile(r"(いつ.*?(から|まで))|((最新|最古).*?(データ|観測))|(何件.*?(観測|データ))"),
        tool="describe_data",
        args={"kind": "date_range"},
    ),
    Rule(
        name="meta-stops",
        pattern=re.compile(r"停留所.*?(いくつ|何個|一覧)"),
        tool="describe_data",
        args={"kind": "stops"},
    ),
    Rule(
        name="meta-agencies",
        pattern=re.compile(r"(何社|何個).*?事業者|事業者.*?(一覧|何社)"),
        tool="describe_data",
        args={"kind": "agencies"},
    ),
    Rule(
        name="meta-overview",
        pattern=re.compile(r"(全体|データセット).*?(概要|概況)|概要を"),
        tool="describe_data",
        args={"kind": "overview"},
    ),
    Rule(
        name="meta-metrics",
        pattern=re.compile(r"(指標|メトリクス|計算できる)"),
        tool="describe_data",
        args={"kind": "metrics"},
    ),
    Rule(
        name="meta-sample-counts",
        pattern=re.compile(r"サンプル数.*?(多い|TOP|ランキング)"),
        tool="describe_data",
        args={"kind": "sample_counts"},
    ),
    # ---- top_n ----
    # NOTE: more-specific ranking rules MUST precede the generic
    # `ranking-worst` rule (first-match-wins) — otherwise e.g.
    # "5分以上の遅れが多い系統TOP10" would be eaten by `ranking-worst`.
    Rule(
        name="ranking-worst-5min",
        pattern=re.compile(r"5分.*?(超|以上).*?(多い|TOP)"),
        tool="top_n",
        args={"metric": "worst_5min", "n": 10},
    ),
    Rule(
        name="ranking-on-time",
        pattern=re.compile(r"定時率.*?(TOP|ランキング|高い)"),
        tool="top_n",
        args={"metric": "on_time_rate", "n": 10},
    ),
    Rule(
        name="ranking-worst",
        pattern=re.compile(r"(遅延|遅れ).*?(ワースト|TOP).*?(\d+)?"),
        tool="top_n",
        args={"metric": "avg_delay", "n": 10},
    ),
    # ---- capabilities fallback for app-help-y phrasings ----
    Rule(
        name="capabilities-help",
        pattern=re.compile(r"(何ができ|使い方|どう使う|使える機能)"),
        tool="capabilities",
        args={},
    ),
]


def _validate_rules() -> None:
    from pipeline.query.tools import _HANDLERS

    known = set(_HANDLERS.keys())
    bad = [r.name for r in _RULES if r.tool not in known]
    if bad:
        raise RuntimeError(f"Phase 2 router has rules pointing at unknown tools: {bad}. Known tools: {sorted(known)}")


_validate_rules()


def _match_rules(question: str) -> RouterDecision | None:
    """Return the first matching rule's decision, or ``None``."""
    if not question:
        return None
    text = question.strip()
    for rule in _RULES:
        if rule.pattern.search(text):
            return RouterDecision(
                stage="rules",
                tool=rule.tool,
                args=dict(rule.args),
                score=1.0,
                matched_pattern=rule.name,
            )
    return None


# Default lives in tests/ask_eval/. Tests override via set_golden_set_path.
_DEFAULT_GOLDEN_SET = Path(__file__).resolve().parents[2] / "tests" / "ask_eval" / "golden_set.jsonl"
_golden_path: Path = _DEFAULT_GOLDEN_SET
_golden_cache: dict[str, tuple[str, dict]] | None = None


def set_golden_set_path(path: Path | None) -> None:
    """Test hook: point the golden-set loader at a different file."""
    global _golden_path, _golden_cache
    _golden_path = path if path is not None else _DEFAULT_GOLDEN_SET
    _golden_cache = None


def _load_golden() -> dict[str, tuple[str, dict]]:
    """``chunk_id → (tool, args)`` mapping from golden_set.jsonl."""
    global _golden_cache
    if _golden_cache is not None:
        return _golden_cache
    import json as _json

    mapping: dict[str, tuple[str, dict]] = {}
    if not _golden_path.exists():
        _log.warning("golden_set.jsonl not found at %s — Stage 2 will produce empty examples", _golden_path)
        _golden_cache = mapping
        return mapping
    with _golden_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = _json.loads(line)
            cid = entry.get("id")
            tool = entry.get("expected_tool")
            args = entry.get("expected_args") or {}
            if cid and tool:
                mapping[cid] = (tool, args)
    _golden_cache = mapping
    return mapping


def _get_embedder():
    """Indirection so tests can monkeypatch."""
    from pipeline.query.embeddings import get_embedder

    return get_embedder()


async def route_question(question: str, conn, agency_id: int) -> RouterDecision | None:
    """Stage 1 + Stage 2 dispatch. Returns ``None`` if no direct dispatch."""
    if not question or not question.strip():
        return None

    decision = _match_rules(question)
    if decision is not None:
        return decision

    embedder = _get_embedder()
    if not getattr(embedder, "available", False):
        return None

    try:
        qvec = embedder.embed(question, mode="query")
    except Exception as exc:
        _log.warning("Stage 2 embed failed: %s — falling through to LLM", exc.__class__.__name__)
        return None

    try:
        from pipeline.query.rag_index import nearest

        matches = await nearest(conn, agency_id, qvec, k=1)
    except Exception as exc:
        _log.warning("Stage 2 nearest failed: %s — falling through to LLM", exc.__class__.__name__)
        return None

    if not matches:
        return None
    top = matches[0]
    if top.distance > _EMBED_DISPATCH_THRESHOLD:
        return None

    golden = _load_golden()
    if top.chunk_id not in golden:
        _log.warning("rag_chunks has chunk_id=%s but golden_set doesn't — falling through", top.chunk_id)
        return None
    tool, args = golden[top.chunk_id]
    return RouterDecision(
        stage="embedding",
        tool=tool,
        args=dict(args),
        score=1.0 - top.distance,
        matched_pattern=top.chunk_id,
    )


async def retrieve_examples(question: str, conn, agency_id: int, k: int = _RAG_TOP_K):
    """Top-``k`` golden-set matches joined to their tool/args.

    Used by the API layer to inject few-shot context into the LLM prompt
    when ``route_question`` returns ``None``. Tolerates an unavailable
    embedder or empty index by returning ``[]`` — caller must handle.
    """
    if not question or not question.strip():
        return []
    embedder = _get_embedder()
    if not getattr(embedder, "available", False):
        return []
    try:
        qvec = embedder.embed(question, mode="query")
    except Exception as exc:
        _log.warning("retrieve_examples embed failed: %s", exc.__class__.__name__)
        return []

    from pipeline.query.rag_index import nearest

    try:
        raw = await nearest(conn, agency_id, qvec, k=k)
    except Exception as exc:
        _log.warning("retrieve_examples nearest failed: %s", exc.__class__.__name__)
        return []

    golden = _load_golden()
    enriched = []
    for m in raw:
        if m.chunk_id in golden:
            tool, args = golden[m.chunk_id]
            from dataclasses import replace

            enriched.append(replace(m, tool=tool, args=dict(args)))
    return enriched
