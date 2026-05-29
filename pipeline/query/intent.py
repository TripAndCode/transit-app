"""Canonical IntentSignature + signature_hash for the Ask cache layer.

Two paraphrased questions that express the same analytical intent must produce
the same dispatch. This module is the deterministic boundary: structure-only
normalization (sort keys, lowercase string enums, drop tool defaults, resolve
relative time tokens), then a stable SHA-256 hash truncated to 16 hex chars.

No I/O, no DB, no LLM — pure functions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# Per-tool defaults — args whose explicit value equals the default are dropped
# before hashing so "n=10 (explicit)" and "n omitted (defaults to 10)" collapse.
# Keep in sync with the actual tool surface in pipeline/query/tools.py.
_TOOL_DEFAULTS: dict[str, dict[str, Any]] = {
    "top_n": {"n": 10, "best_first": False},
    "describe_data": {"limit": 50, "offset": 0, "order": "asc"},
    "time_series": {"granularity": "day"},
    "compare_segments": {},
    "route_stats": {},
    "route_meta": {},
    "capabilities": {},
}

# String enums whose case we normalize; identifiers stay case-preserved.
_LOWERCASE_KEYS = frozenset({"metric", "service_type", "time_window", "granularity", "order"})

# Relative time tokens we resolve to absolute date pairs from the RangeCtx.
_REL_TIME: dict[str, callable] = {
    "last_7_days": lambda ctx: ((ctx["to_date"] - timedelta(days=7)), ctx["to_date"]),
    "last_2_weeks": lambda ctx: ((ctx["to_date"] - timedelta(days=14)), ctx["to_date"]),
    "last_30_days": lambda ctx: ((ctx["to_date"] - timedelta(days=30)), ctx["to_date"]),
}


@dataclass(frozen=True)
class IntentSignature:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str | None = None


def canonicalize(tool: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Produce a stable canonical args dict for hashing.

    ``ctx`` provides the request's ``from_date`` / ``to_date`` (RangeCtx) so
    relative time tokens like ``last_2_weeks`` can resolve to absolute dates
    before hashing. ``ctx`` is consulted only for that purpose.
    """
    if tool not in _TOOL_DEFAULTS:
        raise ValueError(f"unknown tool: {tool!r}")

    out: dict[str, Any] = {}

    # 1. Resolve relative time tokens to absolute dates first.
    if args.get("time_window") in _REL_TIME:
        frm, to = _REL_TIME[args["time_window"]](ctx)
        out["from_date"] = frm.isoformat() if isinstance(frm, date) else str(frm)
        out["to_date"] = to.isoformat() if isinstance(to, date) else str(to)
    else:
        if (v := args.get("from_date")) is not None:
            out["from_date"] = v.isoformat() if isinstance(v, date) else str(v)
        if (v := args.get("to_date")) is not None:
            out["to_date"] = v.isoformat() if isinstance(v, date) else str(v)

    # 2. Copy remaining args, lowercasing string enums and sorting list values.
    for k, v in args.items():
        if k in ("time_window", "from_date", "to_date"):
            continue
        if v is None:
            continue  # treat None and missing as equivalent
        if isinstance(v, str) and k in _LOWERCASE_KEYS:
            out[k] = v.lower()
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = sorted(v)
        else:
            out[k] = v

    # 3. Drop args whose value equals the tool's default.
    for k, default in _TOOL_DEFAULTS[tool].items():
        if out.get(k) == default:
            out.pop(k, None)

    return out


def signature_hash(tool: str, canonical_args: dict[str, Any]) -> str:
    """SHA-256 of the canonical (tool, args) pair, truncated to 16 hex chars."""
    payload = json.dumps(
        {"t": tool, "a": canonical_args},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def derive_confidence(
    nn_distance_same_tool: float | None,
    llm_self_reported: float | None = None,
) -> float:
    """Blend the embedding NN distance and the LLM's self-report into a 0..1 confidence.

    - ``nn_distance_same_tool``: cosine distance to the nearest RAG chunk that
      maps to the same tool the LLM chose. ``None`` means no same-tool match
      was found in the NN window — falls back to a conservative 0.4 floor.
    - ``llm_self_reported``: the model's own confidence (0..1). Clamped to
      [0, 1] before use. ``None`` means no signal — embedding wins.

    Returns the minimum of the two — the lower signal caps the higher one
    (we don't trust the model's self-report alone; the literature is loud on
    that). Embedding-derived = max(0, 1 - distance) when present, else 0.4.
    """
    if nn_distance_same_tool is None:
        embedding_conf = 0.4
    else:
        embedding_conf = max(0.0, 1.0 - float(nn_distance_same_tool))
    if llm_self_reported is None:
        return embedding_conf
    llm_clamped = max(0.0, min(1.0, float(llm_self_reported)))
    return min(embedding_conf, llm_clamped)
