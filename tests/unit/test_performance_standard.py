"""Pure-logic tests for the item-104 bonus/malus formula -- no DB fixtures
(see pipeline.reports.performance_standard's module docstring for the
formula this exercises directly)."""

from pipeline.reports.performance_standard import (
    _relative_deviation,
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
