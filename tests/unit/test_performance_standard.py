"""Pure-logic tests for the performance-standard bonus/malus formula -- no DB fixtures
(see pipeline.reports.performance_standard's module docstring for the
formula this exercises directly)."""

import pytest

from pipeline.reports.performance_standard import (
    _relative_deviation,
    meets_standard,
    simulation_disclaimer,
)


def test_relative_deviation_zero_exactly_at_threshold_lower_is_better():
    assert _relative_deviation("ewt_sec", threshold=50.0, actual=50.0) == 0.0


def test_relative_deviation_negative_when_worse_than_threshold_lower_is_better():
    # ewt_sec is lower-is-better: an actual value ABOVE threshold is worse.
    assert _relative_deviation("ewt_sec", threshold=100.0, actual=150.0) == -0.5


def test_relative_deviation_positive_when_better_than_threshold_lower_is_better():
    assert _relative_deviation("ewt_sec", threshold=100.0, actual=50.0) == 0.5


def test_relative_deviation_zero_exactly_at_threshold_higher_is_better():
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=80.0, actual=80.0) == 0.0


def test_relative_deviation_negative_when_worse_than_threshold_higher_is_better():
    # vehicle_km_delivered_pct is higher-is-better: an actual value BELOW
    # threshold is worse.
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=90.0, actual=80.0) == (80.0 - 90.0) / 90.0


def test_relative_deviation_positive_when_better_than_threshold_higher_is_better():
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=80.0, actual=90.0) == (90.0 - 80.0) / 80.0


def test_relative_deviation_none_for_zero_threshold():
    """A zero threshold has no meaningful percent-of-standard denominator --
    must surface as undefined (None), never raise or fabricate a number."""
    assert _relative_deviation("ewt_sec", threshold=0.0, actual=10.0) is None
    assert _relative_deviation("vehicle_km_delivered_pct", threshold=0.0, actual=10.0) is None


def test_simulation_disclaimer_differs_by_locale_and_defaults_to_japanese():
    ja = simulation_disclaimer("ja")
    en = simulation_disclaimer("en")
    assert ja != en
    assert "シミュレーション" in ja
    assert "simulation" in en.lower()
    # An unsupported/missing locale falls back to Japanese rather than
    # raising a KeyError -- same "always assume a non-empty supported
    # value" convention as api.deps.get_locale.
    assert simulation_disclaimer("fr") == ja


def test_an_on_time_share_above_its_target_is_better():
    assert _relative_deviation("on_time_pct", threshold=80.0, actual=60.0) == -0.25
    assert _relative_deviation("on_time_pct", threshold=80.0, actual=100.0) == 0.25


@pytest.mark.parametrize(
    ("metric_type", "threshold", "actual", "meets"),
    [
        # A share at its target meets it; a tenth of a point under does not.
        ("on_time_pct", 85.0, 85.0, True),
        ("on_time_pct", 85.0, 84.9, False),
        ("on_time_pct", 85.0, 91.2, True),
        # A target of 0 is met by any share, though no achievement rate exists.
        ("on_time_pct", 0.0, 0.0, True),
        ("vehicle_km_delivered_pct", 90.0, 80.0, False),
        # Excess wait is lower-is-better.
        ("ewt_sec", 50.0, 50.0, True),
        ("ewt_sec", 50.0, 51.0, False),
        ("ewt_sec", 50.0, 20.0, True),
    ],
)
def test_meets_standard_reads_each_metrics_direction(metric_type, threshold, actual, meets):
    assert meets_standard(metric_type, threshold, actual) is meets


def test_meets_standard_is_unknown_without_a_figure():
    assert meets_standard("on_time_pct", 85.0, None) is None
