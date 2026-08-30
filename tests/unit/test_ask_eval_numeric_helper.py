"""Offline self-tests for the numeric-ground-truth assertion helper.

Lives here (separate from ``tests/ask_eval/test_synthetic_numeric.py``)
because these tests only exercise
``assert_matches_ground_truth`` against fabricated dicts — no Postgres, no
ClickHouse, no network. The parent ``tests/conftest.py``'s session-scoped,
autouse ``apply_schema`` fixture runs for every test collected under
``tests/`` regardless of whether the test itself touches a DB fixture, so
an unreachable Postgres made these ERROR at fixture setup while they lived
under ``tests/ask_eval/``, not skip or pass. ``tests/unit/conftest.py``
overrides that fixture specifically so tests here bypass it, matching
CLAUDE.md's "Put pure logic tests under tests/unit/" convention.

Proves the numeric check itself isn't vacuous — accepts a correct number,
rejects a wrong number, rejects a wrong tool call — independent of whether
a live LLM or either throwaway database is available.
"""

from __future__ import annotations

import pytest

from tests.ask_eval.numeric_ground_truth import assert_matches_ground_truth, fake_route_stats_response
from tests.fixtures.synthetic_gtfs import uniform_delays


def test_assert_matches_ground_truth_accepts_correct_number():
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    assert_matches_ground_truth(fake_route_stats_response(pattern, correct), pattern)


def test_assert_matches_ground_truth_rejects_wrong_number():
    """Deliberately corrupt the returned avg_min and confirm the check fails —
    guards against this test suite silently passing no matter what number
    comes back (the exact failure mode item 23 exists to catch)."""
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    wrong = (correct or 0.0) + 100.0
    with pytest.raises(AssertionError, match="!= ground truth"):
        assert_matches_ground_truth(fake_route_stats_response(pattern, wrong), pattern)


def test_assert_matches_ground_truth_rejects_wrong_tool():
    pattern = uniform_delays()
    correct = pattern.expected["agg_route_stats"]["avg_min"]
    response_json = fake_route_stats_response(pattern, correct)
    response_json["tool_call"] = {"name": "describe_data", "arguments": {"kind": "routes"}}
    with pytest.raises(AssertionError, match="expected tool_call 'route_stats'"):
        assert_matches_ground_truth(response_json, pattern)
