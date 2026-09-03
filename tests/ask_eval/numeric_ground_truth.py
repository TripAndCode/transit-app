"""Shared assertion helpers for numeric-ground-truth checks.

Split out of ``test_synthetic_numeric.py`` so the pure-logic assertion
helper and its self-tests can live in ``tests/unit/`` (no DB fixtures)
while the live-LLM tests that actually exercise the Ask API stay in
``tests/ask_eval/`` (DB-backed). Neither side re-derives this logic,
keeping one source of truth for "is the number right" across both tiers.
"""

from __future__ import annotations

from tests.fixtures.synthetic_gtfs import SyntheticPattern


def extract_avg_min(response_json: dict, route_code: str, service_type: str) -> float | None:
    """Pull the ``avg_min`` value for *(route_code, service_type)* out of an
    ``/ask`` response's ``result.rows``/``result.columns`` — the shape
    ``pipeline.query.tools._tool_route_stats`` produces (columns
    ``["route_code", "service_type", "dow", "avg_min", "samples"]``).

    Returns ``None`` when there's no matching row at all (wrong tool called,
    tool returned empty, or the route/service_type didn't match) — the
    caller turns that into a clear assertion message rather than a raw
    ``TypeError`` from indexing a missing column.

    Ignores ``dow`` and returns the first matching row, which is only correct
    because every item-21 pattern currently in use puts all of its rows on a
    single calendar day (one ``dow`` group). A future multi-day pattern would
    produce more than one row here and this would need to pick (or average)
    across ``dow`` explicitly instead of taking the first match.
    """
    result = response_json.get("result") or {}
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if "avg_min" not in columns or "route_code" not in columns:
        return None
    idx_avg = columns.index("avg_min")
    idx_route = columns.index("route_code")
    idx_svc = columns.index("service_type") if "service_type" in columns else None
    for row in rows:
        if row[idx_route] != route_code:
            continue
        if idx_svc is not None and row[idx_svc] != service_type:
            continue
        return row[idx_avg]
    return None


def assert_matches_ground_truth(response_json: dict, pattern: SyntheticPattern, places: int = 2) -> None:
    """Assert the API's numeric answer for *pattern* matches its hand-computed
    ``expected["agg_route_stats"]["avg_min"]`` — the same ground truth
    ``tests/pipeline/test_synthetic_agg_e2e.py`` (item 21) asserts against.

    Checks the tool call name first so a wrong-tool failure reads distinctly
    from a wrong-number failure (both are real defects, but the fix differs;
    a model that skips tool dispatch entirely and answers from free text —
    e.g. stale conversation history, item 16's original bug shape — also
    fails here first, since it returns ``tool_call: None``).
    """
    tool_call = response_json.get("tool_call") or {}
    assert tool_call.get("name") == "route_stats", (
        f"{pattern.name}: expected tool_call 'route_stats', got {tool_call.get('name')!r} "
        f"(answer: {response_json.get('answer')!r})"
    )
    expected_avg_min = pattern.expected["agg_route_stats"]["avg_min"]
    assert expected_avg_min is not None, f"{pattern.name}: pattern has no comparable avg_min ground truth"
    actual = extract_avg_min(response_json, pattern.route_code, pattern.service_type)
    assert actual is not None, (
        f"{pattern.name}: no route_stats row for route={pattern.route_code!r} "
        f"service_type={pattern.service_type!r} in response (answer: {response_json.get('answer')!r})"
    )
    assert round(float(actual), places) == expected_avg_min, (
        f"{pattern.name}: API-returned avg_min {actual!r} != ground truth {expected_avg_min!r} "
        f"(answer: {response_json.get('answer')!r})"
    )


def fake_route_stats_response(pattern: SyntheticPattern, avg_min: float) -> dict:
    """Build a fabricated ``/ask``-shaped response for offline assertion tests."""
    return {
        "answer": "テスト",
        "tool_call": {"name": "route_stats", "arguments": {"route": pattern.route_code}},
        "result": {
            "kind": "table",
            "summary": "テスト",
            "rows": [[pattern.route_code, pattern.service_type, "月", avg_min, 25]],
            "columns": ["route_code", "service_type", "dow", "avg_min", "samples"],
            "series": [],
            "pairs": [],
        },
    }
