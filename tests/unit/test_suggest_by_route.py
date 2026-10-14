"""Unit tests for the Insight Panel rule chain's route-keyed views of
compute_ranking / compute_on_time rows
(pipeline/reports/suggest.py::_ranking_by_route / _on_time_by_route).

Pure functions, no DB -- see AGENTS.md's tests/unit/ convention.
"""

from decimal import Decimal

from pipeline.reports.suggest import (
    ON_TIME_FALLBACK_FETCH_LIMIT,
    RANKING_FETCH_LIMIT,
    _on_time_by_route,
    _ranking_by_route,
)


def test_fetch_limits_cover_every_route_of_an_agency():
    """Each rule filters its candidates after the fetch (exclude set, baseline
    match, thresholds), so a fetch cut near an agency's route count could drop
    the route a rule should name. Agencies run hundreds of routes; this keeps
    real headroom above that without needing a DB fixture that large."""
    assert RANKING_FETCH_LIMIT >= 2000
    assert ON_TIME_FALLBACK_FETCH_LIMIT >= 2000


def test_ranking_by_route_keys_each_route_row():
    rows = [
        ("R1", None, Decimal("10.0"), Decimal("9.0"), Decimal("12.0"), 90),
        ("R2", None, Decimal("2.0"), Decimal("1.5"), None, 30),
    ]
    assert _ranking_by_route(rows) == {
        "R1": {"avg_min": 10.0, "p90_min": 12.0, "samples": 90},
        "R2": {"avg_min": 2.0, "p90_min": None, "samples": 30},
    }


def test_on_time_by_route_keys_each_route_row():
    rows = [("R1", "平日", Decimal("74.0"), Decimal("1.0"), 100)]
    assert _on_time_by_route(rows) == {"R1": {"on_time_pct": 74.0, "samples": 100}}
