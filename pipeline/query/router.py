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
    Rule(
        name="ranking-worst",
        pattern=re.compile(r"(遅延|遅れ).*?(ワースト|TOP).*?(\d+)?"),
        tool="top_n",
        args={"metric": "avg_delay", "n": 10},
    ),
    Rule(
        name="ranking-on-time",
        pattern=re.compile(r"定時率.*?(TOP|ランキング|高い)"),
        tool="top_n",
        args={"metric": "on_time_rate", "n": 10},
    ),
    Rule(
        name="ranking-worst-5min",
        pattern=re.compile(r"5分.*?(超|以上).*?(多い|TOP)"),
        tool="top_n",
        args={"metric": "worst_5min", "n": 10},
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
        raise RuntimeError(
            f"Phase 2 router has rules pointing at unknown tools: {bad}. "
            f"Known tools: {sorted(known)}"
        )


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
