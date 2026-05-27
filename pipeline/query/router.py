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

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)

# Distance below which Stage 2 will dispatch directly (cosine distance;
# smaller = closer). Genuine paraphrases cluster ≤0.13; confirmed false
# dispatches landed at 0.13–0.15, so the threshold is tightened to 0.12.
_EMBED_DISPATCH_THRESHOLD = 0.12

# Minimum gap between the top match and the runner-up. When two golden Qs
# are nearly equidistant the top hit is ambiguous, so we decline to
# dispatch and fall through to the LLM (with the same rows as few-shot).
_EMBED_MARGIN = 0.02

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
        # Require enumeration intent so definition questions
        # ("〜という指標の意味") don't over-fire into describe_data.
        pattern=re.compile(r"(指標|メトリクス).*?(一覧|何|計算でき)|計算できる.*?(指標|メトリクス)"),
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
        pattern=re.compile(r"(遅延|遅れ).*?(ワースト|TOP)\s*(\d+)?"),
        tool="top_n",
        args={"metric": "avg_delay", "n": 10},
    ),
    # ---- capabilities fallback for app-help-y phrasings ----
    Rule(
        name="capabilities-help",
        # Anchor at start or after a delimiter so "何ができ" doesn't fire
        # mid-sentence inside an unrelated question.
        pattern=re.compile(r"(^|[、。\s])(何ができ|使い方|どう使う|使える機能)"),
        tool="capabilities",
        args={},
    ),
    # ---- deterministic out-of-scope guard (lowest priority) ----
    # Clearly out-of-scope topics route to capabilities so refusals stay
    # deterministic even when the LLM is unavailable.
    Rule(
        name="oos-guard",
        pattern=re.compile(r"(天気|気温|降水|運賃|料金|定期券|事故|遅延証明|駐車場|忘れ物|時刻表のPDF)"),
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


# Phrasings that only make sense against the previous turn (pagination,
# re-sort, "show me more"). Tightened to avoid two classes of false
# positives that forced needless/failing LLM calls:
#   * もっと must not swallow もっとも ("most" — a superlative, not "more").
#   * 次の/前の must not catch literal "next/previous stop" data questions.
#     They only count as follow-ups before continuation/pagination words.
#   * bare English words must be whole words (\b), so they don't fire inside
#     content words like "same-day" or "next stop".
_FOLLOW_UP_RE = re.compile(
    r"もっと(?!も)"  # もっと but NOT もっとも (superlative "most")
    r"|もう少し|続き|つづき|さらに|残り|他には|さっき"
    r"|次の(?:\d+件|\d*ページ|やつ|分(?!.*停留所)|ぶん)|次のページ|\d+ページ目"  # 次の<page-ish>, not 次の停留所
    r"|前の(?:\d+件|やつ|結果|に戻)"  # 前の<continuation>, not 前の停留所
    r"|同じ(?:条件|系統|の)"
    r"|逆順|逆に|昇順|降順|並べ替え|並び替え|絞り込"
    r"|別の(?!府)"  # 別の but NOT 別府 (place name)
    r"|それを|そのまま"
    # English follow-ups only when trailing (whole-word, end of query) — so
    # "next"/"more" count but content words like "same-day comparison" or
    # "next stop information" do not (favors false-negatives, which are safe).
    r"|\b(?:more|next|again|continue|previous|same|reverse)\b\s*[.!?]?\s*$"
)


def is_follow_up(question: str) -> bool:
    """True when a question only makes sense against the previous turn.

    Stateless Stages 1-2 cannot resolve these (a bare "もっと" has no
    standalone tool mapping), so the API routes them straight to the LLM
    stage with conversation history attached.
    """
    return bool(_FOLLOW_UP_RE.search((question or "").strip()))


def _match_rules(question: str) -> RouterDecision | None:
    """Return the first matching rule's decision, or ``None``."""
    if not question:
        return None
    text = question.strip()
    for rule in _RULES:
        m = rule.pattern.search(text)
        if m:
            args = dict(rule.args)
            # Honor a captured count: if the rule carries an "n" arg and the
            # match captured an all-digit group, override the hardcoded n.
            if "n" in args:
                for g in m.groups():
                    if g is not None and g.isdigit():
                        args["n"] = int(g)
                        break
            return RouterDecision(
                stage="rules",
                tool=rule.tool,
                args=args,
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
            try:
                entry = _json.loads(line)
            except ValueError as exc:
                # A malformed line must not break the "always degrade"
                # contract — log and skip so routing still works.
                _log.warning("golden_set.jsonl: skipping malformed line: %s", exc)
                continue
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


def _enrich(raw, golden):
    """Join raw :class:`Match` rows to their golden_set tool/args."""
    enriched = []
    for m in raw:
        if m.chunk_id in golden:
            tool, args = golden[m.chunk_id]
            enriched.append(replace(m, tool=tool, args=dict(args)))
    return enriched


async def route_or_examples(question, conn, agency_id, k=_RAG_TOP_K):
    """Single embed+search. Returns (decision_or_None, examples_list).

    If the top match is within threshold+margin → (RouterDecision, []).
    Otherwise → (None, top-k enriched examples for the LLM).

    Rules are checked first: a rule hit returns ``(decision, [])`` with no
    embed at all. The router is additive — any failure (empty question,
    embedder down, nearest error, empty index) returns ``(None, [])``.
    """
    if not question or not question.strip():
        return None, []

    decision = _match_rules(question)
    if decision is not None:
        return decision, []

    embedder = _get_embedder()
    if not getattr(embedder, "available", False):
        return None, []

    try:
        # encode() is CPU-bound — keep it off the shared event loop.
        qvec = await asyncio.to_thread(embedder.embed, question, mode="query")
    except Exception as exc:
        _log.warning("Stage 2 embed failed: %s — falling through to LLM", exc.__class__.__name__)
        return None, []

    try:
        from pipeline.query.rag_index import nearest

        # Fetch enough rows to both decide dispatch (needs top-2 for the
        # margin guard) and serve the few-shot examples on fall-through.
        matches = await nearest(conn, agency_id, qvec, k=max(2, k))
    except Exception as exc:
        _log.warning("Stage 2 nearest failed: %s — falling through to LLM", exc.__class__.__name__)
        return None, []

    if not matches:
        return None, []

    golden = _load_golden()
    top = matches[0]
    within_threshold = top.distance <= _EMBED_DISPATCH_THRESHOLD
    # The margin guard only matters under genuine TOOL ambiguity — when the
    # runner-up maps to a *different* tool and sits within _EMBED_MARGIN of
    # the top. Two near-identical paraphrases of the SAME tool (e.g. several
    # "<route>の遅延" route_stats chunks) must not be blocked by a tiny gap.
    ambiguous = False
    if within_threshold and len(matches) >= 2 and (matches[1].distance - top.distance) < _EMBED_MARGIN:
        top_tool = golden.get(top.chunk_id, (None, None))[0]
        second_tool = golden.get(matches[1].chunk_id, (None, None))[0]
        ambiguous = top_tool != second_tool
    dispatch_ok = within_threshold and not ambiguous
    if dispatch_ok:
        if top.chunk_id not in golden:
            _log.warning("rag_chunks has chunk_id=%s but golden_set doesn't — falling through", top.chunk_id)
        else:
            tool, args = golden[top.chunk_id]
            decision = RouterDecision(
                stage="embedding",
                tool=tool,
                args=dict(args),
                score=1.0 - top.distance,
                matched_pattern=top.chunk_id,
            )
            return decision, []

    # Fall-through: serve top-k enriched examples for the LLM few-shot.
    return None, _enrich(matches[:k], golden)


async def route_question(question: str, conn, agency_id: int) -> RouterDecision | None:
    """Stage 1 + Stage 2 dispatch. Returns ``None`` if no direct dispatch.

    Thin wrapper over :func:`route_or_examples` for backward-compat.
    """
    decision, _ = await route_or_examples(question, conn, agency_id)
    return decision


async def retrieve_examples(question: str, conn, agency_id: int, k: int = _RAG_TOP_K):
    """Top-``k`` golden-set matches joined to their tool/args.

    Used by the API layer to inject few-shot context into the LLM prompt
    when ``route_question`` returns ``None``. Tolerates an unavailable
    embedder or empty index by returning ``[]`` — caller must handle.

    Thin wrapper over :func:`route_or_examples`; returns ``[]`` whenever a
    direct dispatch was possible (rule hit or embedding hit).
    """
    _, examples = await route_or_examples(question, conn, agency_id, k=k)
    return examples
