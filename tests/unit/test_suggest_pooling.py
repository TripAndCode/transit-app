"""Unit tests for the Insight Panel rule chain's route-grain pooling helpers
(pipeline/reports/suggest.py::_pool_ranking_by_route / _pool_on_time_by_route).

Pure functions, no DB -- see CLAUDE.md's tests/unit/ convention. These cover
finding #3 from the final branch review: compute_ranking/compute_on_time
return one row per (route_code, service_type) pair (a route commonly has
~3 service-type variants on real data), and the old rule chain collapsed
that to one row per route_code via last-wins dict construction on an
avg-descending-sorted list -- which systematically picked the route's
LOWEST-avg variant, not a genuine route-grain figure.
"""

from decimal import Decimal

from pipeline.reports.suggest import _pool_on_time_by_route, _pool_ranking_by_route


def test_pool_ranking_by_route_is_sample_weighted_not_last_wins():
    """Two service-type rows for the same route, in the avg-descending order
    compute_ranking actually returns them in. The old
    `{r[0]: r[2] for r in baseline_rows}` dict construction would silently
    pick whichever row sorted last -- here, the 1.0-avg row, since it's
    smaller and compute_ranking sorts avg-descending. The correct
    sample-weighted pool is neither naive pick: (10.0*90 + 1.0*10) / 100 = 9.1.
    """
    rows = [
        ("R1", "svc_a", Decimal("10.0"), Decimal("9.0"), Decimal("12.0"), 90),
        ("R1", "svc_b", Decimal("1.0"), Decimal("0.9"), Decimal("1.2"), 10),
    ]
    pooled = _pool_ranking_by_route(rows)

    assert pooled.keys() == {"R1"}
    assert pooled["R1"]["avg_min"] == 9.1
    assert pooled["R1"]["avg_min"] != 1.0  # not the naive last-wins pick
    assert pooled["R1"]["avg_min"] != 10.0  # not the naive first-row pick
    assert pooled["R1"]["samples"] == 100


def test_pool_ranking_by_route_p90_weighted_same_as_avg():
    rows = [
        ("R1", "svc_a", Decimal("5.0"), Decimal("4.0"), Decimal("8.0"), 20),
        ("R1", "svc_b", Decimal("5.0"), Decimal("4.0"), Decimal("2.0"), 20),
    ]
    pooled = _pool_ranking_by_route(rows)
    assert pooled["R1"]["p90_min"] == 5.0  # (8*20 + 2*20) / 40


def test_pool_ranking_by_route_handles_none_p90():
    """A None p90_min (e.g. an all-tied partition -- see
    rankings.py::_ranking_live's docstring) must not poison the pooled
    average for other service-type rows that do have a p90."""
    rows = [
        ("R1", "svc_a", Decimal("5.0"), Decimal("4.0"), None, 20),
        ("R1", "svc_b", Decimal("5.0"), Decimal("4.0"), Decimal("6.0"), 20),
    ]
    pooled = _pool_ranking_by_route(rows)
    assert pooled["R1"]["p90_min"] == 6.0  # only the non-None row contributes

    all_none_rows = [("R2", "svc_a", Decimal("5.0"), Decimal("4.0"), None, 20)]
    pooled_none = _pool_ranking_by_route(all_none_rows)
    assert pooled_none["R2"]["p90_min"] is None


def test_pool_ranking_by_route_keeps_routes_independent():
    rows = [
        ("R1", "svc_a", Decimal("10.0"), Decimal("9.0"), Decimal("12.0"), 30),
        ("R2", "svc_a", Decimal("2.0"), Decimal("1.5"), Decimal("3.0"), 30),
    ]
    pooled = _pool_ranking_by_route(rows)
    assert pooled.keys() == {"R1", "R2"}
    assert pooled["R1"]["avg_min"] == 10.0
    assert pooled["R2"]["avg_min"] == 2.0


def test_pool_on_time_by_route_is_sample_weighted():
    rows = [
        ("R1", "svc_a", Decimal("90.0"), Decimal("1.0"), 80),
        ("R1", "svc_b", Decimal("10.0"), Decimal("9.0"), 20),
    ]
    pooled = _pool_on_time_by_route(rows)
    # (90*80 + 10*20) / 100 = 74.0 -- genuinely pooled, not either raw row.
    assert pooled["R1"]["on_time_pct"] == 74.0
    assert pooled["R1"]["samples"] == 100
