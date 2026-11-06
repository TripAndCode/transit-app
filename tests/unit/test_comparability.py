"""The cross-agency board's comparability gate: which agencies the data lets it rank."""

import pytest

from pipeline.reports.comparability import (
    MAX_CI_HALF_WIDTH_PP,
    MAX_CLAMP_PCT,
    MIN_COVERAGE_PCT,
    assess,
    observation_coverage,
    on_time_interval,
)

# Every check passes with these; each test changes one input.
_PASS = {
    "ingest_strategy": "static_join",
    "coverage_pct": 92.0,
    "ci_low": 84.9,
    "ci_high": 87.5,
    "clamp_pct": 0.2,
    "samples": 20_000,
}


def _assess(**over):
    return assess(**{**_PASS, **over})


def test_the_thresholds_are_the_published_ones():
    assert (MIN_COVERAGE_PCT, MAX_CI_HALF_WIDTH_PP, MAX_CLAMP_PCT) == (70.0, 3.0, 5.0)


def test_dense_well_covered_data_is_comparable():
    assert _assess() == (True, None)


def test_no_samples_is_no_data():
    assert _assess(samples=0) == (False, "no_data")


def test_no_data_comes_before_every_other_reason():
    failing_everything = {
        "ingest_strategy": "aomori_regex",
        "coverage_pct": None,
        "ci_low": None,
        "ci_high": None,
        "clamp_pct": 50.0,
        "samples": 0,
    }
    assert assess(**failing_everything) == (False, "no_data")


@pytest.mark.parametrize("strategy", ["aomori_regex", None, "something_new"])
def test_a_strategy_whose_trips_cannot_be_matched_to_the_timetable_is_coverage_unknown(strategy):
    assert _assess(ingest_strategy=strategy) == (False, "coverage_unknown")


def test_no_coverage_figure_is_coverage_unknown():
    assert _assess(coverage_pct=None) == (False, "coverage_unknown")


def test_coverage_unknown_comes_before_a_wide_interval_and_high_clamp():
    assert _assess(coverage_pct=None, ci_low=10.0, ci_high=90.0, clamp_pct=40.0) == (False, "coverage_unknown")


def test_coverage_exactly_at_the_floor_passes():
    assert _assess(coverage_pct=70.0) == (True, None)


def test_coverage_below_the_floor_is_coverage_low():
    assert _assess(coverage_pct=69.9) == (False, "coverage_low")


def test_coverage_over_100_passes():
    """Over 100% means the feed reported trips the plan doesn't name; the
    figure stays visible but it is not too little data."""
    assert _assess(coverage_pct=138.8) == (True, None)


def test_coverage_low_comes_before_a_wide_interval():
    assert _assess(coverage_pct=41.0, ci_low=40.0, ci_high=60.0) == (False, "coverage_low")


@pytest.mark.parametrize(("low", "high"), [(80.0, 86.0), (2.3, 8.3), (58.4, 64.4)])
def test_an_interval_exactly_3_points_either_side_passes(low, high):
    """The pairs after the first subtract to a hair over 6.0 in binary
    floating point; the gate reads the half-width a reader would work out
    from the two printed figures."""
    assert _assess(ci_low=low, ci_high=high) == (True, None)


def test_an_interval_wider_than_3_points_either_side_is_ci_wide():
    assert _assess(ci_low=80.0, ci_high=86.2) == (False, "ci_wide")


def test_no_interval_is_ci_wide():
    assert _assess(ci_low=None, ci_high=None) == (False, "ci_wide")


def test_ci_wide_comes_before_high_clamp():
    assert _assess(ci_low=70.0, ci_high=90.0, clamp_pct=20.0) == (False, "ci_wide")


def test_clamp_exactly_at_the_ceiling_passes():
    assert _assess(clamp_pct=5.0) == (True, None)


def test_clamp_over_the_ceiling_is_clamp_high():
    assert _assess(clamp_pct=5.01) == (False, "clamp_high")


def test_no_clamp_share_passes():
    """No feed-health row is an aggregate gap, not evidence of implausible readings."""
    assert _assess(clamp_pct=None) == (True, None)


def test_coverage_is_observed_over_planned_to_one_decimal():
    assert observation_coverage("static_join", 900, 1000) == 90.0
    assert observation_coverage("static_join", 2, 3) == 66.7


def test_coverage_over_100_is_not_clamped():
    assert observation_coverage("static_join", 1388, 1000) == 138.8


def test_coverage_needs_a_plan_a_count_and_a_matchable_strategy():
    assert observation_coverage("static_join", 0, 1000) == 0.0
    assert observation_coverage("static_join", None, 1000) is None
    assert observation_coverage("static_join", 0, 0) is None
    assert observation_coverage("aomori_regex", 900, 1000) is None
    assert observation_coverage(None, 900, 1000) is None


def test_the_interval_counts_trips_not_stop_events():
    """18,000 of 20,000 stop events on time is 90%; over 900 trips the 95%
    Wilson interval is about ±2 points, where 20,000 independent events
    would claim about ±0.4."""
    low, high = on_time_interval(18_000, 20_000, 900)
    assert (low, high) == (87.8, 91.8)
    assert low < 90.0 < high


def test_the_interval_rounds_outward_so_it_never_reads_narrower_than_computed():
    from pipeline.stats import wilson_interval

    low, high = on_time_interval(450, 900, 45)
    exact_low, exact_high = wilson_interval(round(0.5 * 45), 45)
    assert low <= exact_low * 100 and high >= exact_high * 100
    assert high - low < (exact_high - exact_low) * 100 + 0.2


def test_no_interval_without_samples_or_a_trip_count():
    assert on_time_interval(0, 0, 900) == (None, None)
    assert on_time_interval(18_000, 20_000, None) == (None, None)
    assert on_time_interval(18_000, 20_000, 0) == (None, None)
