"""Canonicalization + signature_hash unit tests."""

from datetime import date

import pytest

from pipeline.query.intent import IntentSignature, canonicalize, signature_hash


def _ctx(from_d="2026-05-13", to_d="2026-05-27"):
    return {"from_date": date.fromisoformat(from_d), "to_date": date.fromisoformat(to_d)}


# --- Same-intent pairs that MUST collapse to the same hash ---


def test_paraphrase_top_n_collapses():
    a = canonicalize("top_n", {"metric": "AVG_DELAY", "n": 10}, _ctx())
    b = canonicalize("top_n", {"n": 10, "metric": "avg_delay"}, _ctx())  # key order + case
    assert signature_hash("top_n", a) == signature_hash("top_n", b)


def test_default_n_collapses_with_explicit_default():
    """When n=10 is the tool's default, omitting it must hash the same as explicit n=10."""
    a = canonicalize("top_n", {"metric": "avg_delay"}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay", "n": 10}, _ctx())
    assert signature_hash("top_n", a) == signature_hash("top_n", b)


def test_relative_dates_resolve_before_hashing():
    """'last_2_weeks' resolves to absolute dates from the RangeCtx (to_date − 14 days)."""
    ctx = _ctx("2026-05-13", "2026-05-27")
    a = canonicalize("top_n", {"metric": "avg_delay", "time_window": "last_2_weeks"}, ctx)
    b = canonicalize("top_n", {"metric": "avg_delay", "from_date": "2026-05-13", "to_date": "2026-05-27"}, ctx)
    assert signature_hash("top_n", a) == signature_hash("top_n", b)


def test_route_ids_list_order_collapses():
    """A list of route IDs is order-agnostic for canonicalization."""
    a = canonicalize("compare_segments", {"route_ids": ["16071", "22171"]}, _ctx())
    b = canonicalize("compare_segments", {"route_ids": ["22171", "16071"]}, _ctx())
    assert signature_hash("compare_segments", a) == signature_hash("compare_segments", b)


def test_best_first_default_false_collapses():
    """best_first=False (default) and omitted hash the same."""
    a = canonicalize("top_n", {"metric": "avg_delay"}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay", "best_first": False}, _ctx())
    assert signature_hash("top_n", a) == signature_hash("top_n", b)


# --- Different-intent pairs that MUST NOT collapse ---


def test_different_n_does_not_collapse():
    a = canonicalize("top_n", {"metric": "avg_delay", "n": 10}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay", "n": 20}, _ctx())
    assert signature_hash("top_n", a) != signature_hash("top_n", b)


def test_different_service_type_does_not_collapse():
    a = canonicalize("top_n", {"metric": "avg_delay", "service_type": "weekday"}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay", "service_type": "weekend"}, _ctx())
    assert signature_hash("top_n", a) != signature_hash("top_n", b)


def test_different_tool_does_not_collapse():
    a = canonicalize("top_n", {"metric": "avg_delay"}, _ctx())
    b = canonicalize("time_series", {"metric": "avg_delay"}, _ctx())
    assert signature_hash("top_n", a) != signature_hash("time_series", b)


def test_different_date_range_does_not_collapse():
    _a = canonicalize("top_n", {"metric": "avg_delay"}, _ctx("2026-05-13", "2026-05-27"))
    _b = canonicalize("top_n", {"metric": "avg_delay"}, _ctx("2026-04-13", "2026-04-27"))
    # No explicit dates in args; ctx-resolved range injected only when args reference dates.
    # Both may hash the same — that's valid. The test of record: different EXPLICIT
    # date ranges in args do not collapse.
    a2 = canonicalize("top_n", {"metric": "avg_delay", "from_date": "2026-05-13", "to_date": "2026-05-27"}, _ctx())
    b2 = canonicalize("top_n", {"metric": "avg_delay", "from_date": "2026-04-13", "to_date": "2026-04-27"}, _ctx())
    assert signature_hash("top_n", a2) != signature_hash("top_n", b2)


def test_best_first_true_does_not_collapse_with_default():
    a = canonicalize("top_n", {"metric": "avg_delay"}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay", "best_first": True}, _ctx())
    assert signature_hash("top_n", a) != signature_hash("top_n", b)


# --- Identifier / form details ---


def test_route_id_case_preserved():
    """Identifiers must NOT be lowercased — different-case route IDs must NOT collapse."""
    a = canonicalize("route_stats", {"route_id": "ROUTE_ABC"}, _ctx())
    b = canonicalize("route_stats", {"route_id": "route_abc"}, _ctx())
    assert signature_hash("route_stats", a) != signature_hash("route_stats", b)


def test_hash_is_16_hex_chars():
    h = signature_hash("top_n", canonicalize("top_n", {"metric": "avg_delay"}, _ctx()))
    assert len(h) == 16 and all(c in "0123456789abcdef" for c in h)


def test_unknown_tool_raises():
    with pytest.raises(ValueError):
        canonicalize("not_a_tool", {}, _ctx())


def test_signature_dataclass_frozen():
    sig = IntentSignature(tool="top_n", args={"metric": "avg_delay"}, confidence=0.9)
    with pytest.raises(Exception):
        sig.tool = "time_series"  # frozen dataclass


def test_null_and_missing_are_equivalent():
    """A None-valued arg and an absent arg must produce the same canonical form."""
    a = canonicalize("top_n", {"metric": "avg_delay", "service_type": None}, _ctx())
    b = canonicalize("top_n", {"metric": "avg_delay"}, _ctx())
    assert signature_hash("top_n", a) == signature_hash("top_n", b)


def test_date_object_args_serialize_to_iso():
    """Passing date objects (not strings) for from_date/to_date works and hashes the same."""
    a = canonicalize(
        "top_n",
        {"metric": "avg_delay", "from_date": date(2026, 5, 13), "to_date": date(2026, 5, 27)},
        _ctx(),
    )
    b = canonicalize(
        "top_n",
        {"metric": "avg_delay", "from_date": "2026-05-13", "to_date": "2026-05-27"},
        _ctx(),
    )
    assert signature_hash("top_n", a) == signature_hash("top_n", b)
