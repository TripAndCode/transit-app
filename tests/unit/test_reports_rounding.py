"""Pure-logic test: agg-path minute rounding matches Postgres ROUND."""

from decimal import Decimal

from pipeline.reports.filters import _round2
from pipeline.reports.rankings import _avg_min


def test_avg_min_rounds_half_away_from_zero():
    # 603/10/60 = 1.005 → Postgres ROUND(.,2) gives 1.01 (half away from zero).
    # Decimal's default ROUND_HALF_EVEN would give 1.00 — the bug this guards.
    assert _avg_min(603, 10) == Decimal("1.01")


def test_avg_min_exact_value():
    # 7200s over 60 samples = 120s = 2.00 min.
    assert _avg_min(7200, 60) == Decimal("2.00")


def test_round2_rounds_half_away_from_zero():
    # Shared by overview.py and rankings.py (pipeline/reports/filters.py) —
    # same half-up rounding _avg_min guards against, on an already-in-minutes
    # float rather than a raw seconds/samples pair.
    assert _round2(1.005) == Decimal("1.01")


def test_round2_exact_value():
    assert _round2(2.0) == Decimal("2.00")
