"""Paraphrase pairs: same intent → same signature_hash (and the negative twin).

These tests bypass the LLM entirely — we exercise the canonical-form layer
directly. The premise: when the LLM eventually maps two differently-worded
questions to the same (tool, args), canonicalize() must produce the same
hash. And when the user means something semantically different, the hashes
must differ.

If a pair is added that fails because canonicalize is "too aggressive"
(collapses things that mean different things) OR "too lax" (lets two
intents through to different hashes when they shouldn't), this file is the
record of which rule needs tightening or loosening.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.query.intent import canonicalize, signature_hash


def _ctx(from_d="2026-05-13", to_d="2026-05-27"):
    return {"from_date": date.fromisoformat(from_d), "to_date": date.fromisoformat(to_d)}


def _hash(tool, args, ctx=None):
    return signature_hash(tool, canonicalize(tool, args, ctx or _ctx()))


# -- 10 paraphrase pairs that MUST collapse -------------------------------------
# Each tuple: (description, (tool_a, args_a), (tool_b, args_b))

PARAPHRASE_PAIRS = [
    (
        "top_n: case + key order differ",
        ("top_n", {"metric": "AVG_DELAY", "n": 10}),
        ("top_n", {"n": 10, "metric": "avg_delay"}),
    ),
    (
        "top_n: default n omitted vs explicit",
        ("top_n", {"metric": "avg_delay"}),
        ("top_n", {"metric": "avg_delay", "n": 10}),
    ),
    (
        "top_n: relative time vs explicit absolute dates (same window)",
        ("top_n", {"metric": "avg_delay", "time_window": "last_2_weeks"}),
        ("top_n", {"metric": "avg_delay", "from_date": "2026-05-13", "to_date": "2026-05-27"}),
    ),
    (
        "top_n: best_first=False default vs omitted",
        ("top_n", {"metric": "avg_delay"}),
        ("top_n", {"metric": "avg_delay", "best_first": False}),
    ),
    (
        "compare_segments: route_ids list order",
        ("compare_segments", {"route_ids": ["16071", "22171"]}),
        ("compare_segments", {"route_ids": ["22171", "16071"]}),
    ),
    (
        "describe_data: default offset/limit/order omitted vs explicit",
        ("describe_data", {"kind": "stops"}),
        ("describe_data", {"kind": "stops", "offset": 0, "limit": 50, "order": "asc"}),
    ),
    (
        "route_stats: nothing to canonicalize but identifiers preserved",
        ("route_stats", {"route_id": "16071"}),
        ("route_stats", {"route_id": "16071"}),
    ),
    (
        "top_n: service_type case + None-valued arg dropped",
        ("top_n", {"metric": "avg_delay", "service_type": "WEEKDAY"}),
        ("top_n", {"metric": "avg_delay", "service_type": "weekday", "from_date": None}),
    ),
    (
        "time_series: default granularity=day omitted vs explicit",
        ("time_series", {"metric": "avg_delay"}),
        ("time_series", {"metric": "avg_delay", "granularity": "day"}),
    ),
    (
        "top_n: relative last_7_days resolves to absolute (today's ctx)",
        ("top_n", {"metric": "avg_delay", "time_window": "last_7_days"}),
        ("top_n", {"metric": "avg_delay", "from_date": "2026-05-20", "to_date": "2026-05-27"}),
    ),
]


# -- 5 negative pairs that MUST NOT collapse ------------------------------------

NEGATIVE_PAIRS = [
    (
        "different tool entirely",
        ("top_n", {"metric": "avg_delay"}),
        ("time_series", {"metric": "avg_delay"}),
    ),
    (
        "different metric within same tool",
        ("top_n", {"metric": "avg_delay"}),
        ("top_n", {"metric": "on_time_rate"}),
    ),
    (
        "different n (10 vs 20)",
        ("top_n", {"metric": "avg_delay", "n": 10}),
        ("top_n", {"metric": "avg_delay", "n": 20}),
    ),
    (
        "different service_type",
        ("top_n", {"metric": "avg_delay", "service_type": "weekday"}),
        ("top_n", {"metric": "avg_delay", "service_type": "weekend"}),
    ),
    (
        "best_first flip — 'top' vs 'bottom' is a different question",
        ("top_n", {"metric": "avg_delay"}),
        ("top_n", {"metric": "avg_delay", "best_first": True}),
    ),
]


@pytest.mark.parametrize(
    "desc,call_a,call_b",
    PARAPHRASE_PAIRS,
    ids=[p[0] for p in PARAPHRASE_PAIRS],
)
def test_paraphrase_pair_collapses(desc, call_a, call_b):
    """Same canonical intent → same signature_hash, regardless of wording."""
    ha = _hash(*call_a)
    hb = _hash(*call_b)
    assert ha == hb, f"{desc}: {call_a} hashed {ha}, {call_b} hashed {hb}"


@pytest.mark.parametrize(
    "desc,call_a,call_b",
    NEGATIVE_PAIRS,
    ids=[p[0] for p in NEGATIVE_PAIRS],
)
def test_negative_pair_does_not_collapse(desc, call_a, call_b):
    """Semantically different intents must not share a signature_hash."""
    ha = _hash(*call_a)
    hb = _hash(*call_b)
    assert ha != hb, f"{desc}: both hashed to {ha}"
