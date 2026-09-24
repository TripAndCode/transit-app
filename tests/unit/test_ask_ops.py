"""Pure-logic tests for the admin Ask-ops route/status derivation and the
funnel aggregation helper — no DB, matches CLAUDE.md's tests/unit convention.
"""

from __future__ import annotations

from pipeline.query.ask_ops import (
    ROUTE_ORDER,
    build_funnel,
    derive_route,
    derive_status,
    find_latest_eval_result,
    route_to_stage,
    status_to_success,
)


def test_derive_route_maps_known_stages():
    assert derive_route("rules") == "rules"
    assert derive_route("embedding") == "nn"
    assert derive_route("llm") == "rag"
    assert derive_route("no_history") == "no_history"


def test_derive_route_passes_through_unknown_stage():
    assert derive_route("some_future_stage") == "some_future_stage"


def test_route_to_stage_is_inverse_of_derive_route():
    for route in ("rules", "nn", "rag", "no_history"):
        assert derive_route(route_to_stage(route)) == route


def test_route_to_stage_rejects_unknown_route():
    assert route_to_stage("bogus") is None


def test_derive_status_maps_success_bool():
    assert derive_status(True) == "ok"
    assert derive_status(False) == "error"


def test_status_to_success_roundtrip_and_rejects_unknown():
    assert status_to_success("ok") is True
    assert status_to_success("error") is False
    assert status_to_success("bogus") is None


def test_build_funnel_empty_input_has_zeroed_known_routes():
    funnel = build_funnel([])
    assert funnel.total == 0
    assert [r.route for r in funnel.by_route] == list(ROUTE_ORDER)
    assert all(r.count == 0 and r.success_count == 0 for r in funnel.by_route)


def test_build_funnel_aggregates_and_orders_routes():
    raw = [
        ("llm", True, 7),
        ("llm", False, 3),
        ("rules", True, 20),
        ("embedding", True, 5),
        ("embedding", False, 1),
        ("no_history", False, 2),
    ]
    funnel = build_funnel(raw)
    assert funnel.total == 20 + 5 + 1 + 7 + 3 + 2
    by_route = {r.route: r for r in funnel.by_route}
    assert [r.route for r in funnel.by_route] == list(ROUTE_ORDER)
    assert by_route["rules"].count == 20
    assert by_route["rules"].success_count == 20
    assert by_route["nn"].count == 6
    assert by_route["nn"].success_count == 5
    assert by_route["rag"].count == 10
    assert by_route["rag"].success_count == 7
    assert by_route["no_history"].count == 2
    assert by_route["no_history"].success_count == 0


def test_build_funnel_merges_duplicate_router_stage_rows():
    """Two GROUP BY rows for the same (stage, success) pair (shouldn't
    normally happen, but the aggregation must not silently drop one)."""
    raw = [("rules", True, 3), ("rules", True, 4)]
    funnel = build_funnel(raw)
    by_route = {r.route: r for r in funnel.by_route}
    assert by_route["rules"].count == 7


def test_find_latest_eval_result_none_when_dir_missing(tmp_path):
    assert find_latest_eval_result(tmp_path / "does-not-exist") is None


def test_find_latest_eval_result_none_when_no_matching_files(tmp_path):
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")
    assert find_latest_eval_result(tmp_path) is None


def test_find_latest_eval_result_reads_lexicographically_latest(tmp_path):
    (tmp_path / "ask-eval-2026-09-07.json").write_text('{"score": 0.5}', encoding="utf-8")
    (tmp_path / "ask-eval-2026-09-14.json").write_text('{"score": 0.9}', encoding="utf-8")
    result = find_latest_eval_result(tmp_path)
    assert result == {"score": 0.9}


def test_find_latest_eval_result_none_on_malformed_json(tmp_path):
    (tmp_path / "ask-eval-2026-09-14.json").write_text("not json", encoding="utf-8")
    assert find_latest_eval_result(tmp_path) is None
