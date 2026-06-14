"""Pure-logic test: agg-path minute rounding matches Postgres ROUND."""

from decimal import Decimal

from pipeline.reports.rankings import _avg_min


def test_avg_min_rounds_half_away_from_zero():
    # 603/10/60 = 1.005 → Postgres ROUND(.,2) gives 1.01 (half away from zero).
    # Decimal's default ROUND_HALF_EVEN would give 1.00 — the bug this guards.
    assert _avg_min(603, 10) == Decimal("1.01")


def test_avg_min_exact_value():
    # 7200s over 60 samples = 120s = 2.00 min.
    assert _avg_min(7200, 60) == Decimal("2.00")
