"""The cross-agency board's comparability gate: which agencies the data lets it rank."""

import pytest

from pipeline.reports.comparability import (
    MAX_CI_HALF_WIDTH_PP,
    MAX_CLAMP_PCT,
    MIN_COVERAGE_PCT,
    _first_failure,
    _on_time_interval,
    assess,
    ci_half_width_as_printed,
    clamp_pct,
    coverage_pct,
    tenth_as_printed,
    trips_matchable,
)

# Every check passes with these; each test changes one input.
_PASS = {
    "coverage_pct": 92.0,
    "ci_low": 84.9,
    "ci_high": 87.5,
    "clamp_pct": 0.2,
    "samples": 20_000,
}


def _verdict(**over):
    return _first_failure(**{**_PASS, **over})


def test_the_thresholds_are_the_published_ones():
    assert (MIN_COVERAGE_PCT, MAX_CI_HALF_WIDTH_PP, MAX_CLAMP_PCT) == (70.0, 3.0, 5.0)


def test_dense_well_covered_data_is_comparable():
    assert _verdict() is None


def test_no_samples_is_no_data():
    assert _verdict(samples=0) == "no_data"


def test_no_data_comes_before_every_other_reason():
    assert _first_failure(coverage_pct=None, ci_low=None, ci_high=None, clamp_pct=50.0, samples=0) == "no_data"


def test_no_coverage_figure_is_coverage_unknown():
    assert _verdict(coverage_pct=None) == "coverage_unknown"


def test_coverage_unknown_comes_before_a_wide_interval_and_high_clamp():
    assert _verdict(coverage_pct=None, ci_low=10.0, ci_high=90.0, clamp_pct=40.0) == "coverage_unknown"


def test_coverage_exactly_at_the_floor_passes():
    assert _verdict(coverage_pct=70.0) is None


def test_coverage_below_the_floor_is_coverage_low():
    assert _verdict(coverage_pct=69.9) == "coverage_low"


def test_coverage_over_100_passes():
    """Over 100% means the feed reported trips the plan doesn't name; the
    figure stays visible but it is not too little data."""
    assert _verdict(coverage_pct=138.8) is None


def test_coverage_low_comes_before_a_wide_interval():
    assert _verdict(coverage_pct=41.0, ci_low=40.0, ci_high=60.0) == "coverage_low"


@pytest.mark.parametrize(("low", "high"), [(80.0, 86.0), (2.3, 8.3), (58.4, 64.4)])
def test_an_interval_exactly_3_points_either_side_passes(low, high):
    """The pairs after the first subtract to a hair over 6.0 in binary
    floating point; the gate reads the half-width a reader would work out
    from the two printed figures."""
    assert _verdict(ci_low=low, ci_high=high) is None


def test_an_interval_wider_than_3_points_either_side_is_ci_wide():
    assert _verdict(ci_low=80.0, ci_high=86.2) == "ci_wide"


def test_no_interval_is_ci_wide():
    assert _verdict(ci_low=None, ci_high=None) == "ci_wide"


def test_ci_wide_comes_before_high_clamp():
    assert _verdict(ci_low=70.0, ci_high=90.0, clamp_pct=20.0) == "ci_wide"


@pytest.mark.parametrize("clamp", [5.0, 5.04])
def test_a_clamp_share_printed_at_the_ceiling_passes(clamp):
    """The board prints the share to a tenth, so 5.04 reads 5.0%, which is
    not above 5%."""
    assert _verdict(clamp_pct=clamp) is None


@pytest.mark.parametrize("clamp", [5.05, 5.1, 12.0])
def test_a_clamp_share_printed_over_the_ceiling_is_clamp_high(clamp):
    """5.05 prints as 5.1%, so the reason never shows a figure at the limit."""
    assert _verdict(clamp_pct=clamp) == "clamp_high"


def test_no_clamp_share_passes():
    """No feed-health row is an aggregate gap, not evidence of implausible readings."""
    assert _verdict(clamp_pct=None) is None


@pytest.mark.parametrize(
    ("value", "printed"),
    [(5.04, 5.0), (5.05, 5.1), (0.05, 0.1), (0.04, 0.0), (1.25, 1.3), (2.35, 2.4), (-0.05, -0.1), (7.0, 7.0)],
)
def test_a_tenth_rounds_half_away_from_zero_as_the_board_prints_it(value, printed):
    """frontend/src/utils/format.ts roundTo: Math.round of the binary value
    times ten, ties away from zero; Python's round() would take 2.35 to 2.3."""
    assert tenth_as_printed(value) == printed


@pytest.mark.parametrize(
    ("low", "high", "printed"),
    [(80.0, 86.1, 3.1), (80.0, 86.2, 3.1), (2.3, 8.3, 3.0), (58.4, 64.4, 3.0), (40.0, 60.0, 10.0), (84.9, 87.5, 1.3)],
)
def test_an_interval_half_width_prints_rounded_up_as_the_board_prints_it(low, high, printed):
    """frontend/src/components/comparabilityReason.ts ciHalfWidth: half the printed bounds'
    difference, rounded up to a tenth, so an interval the gate found wider
    than its limit never prints at the limit."""
    assert ci_half_width_as_printed(low, high) == printed


def test_coverage_is_observed_over_planned_to_one_decimal():
    assert coverage_pct(900, 1000) == 90.0
    assert coverage_pct(2, 3) == 66.7


def test_coverage_over_100_is_not_clamped():
    assert coverage_pct(1388, 1000) == 138.8


def test_coverage_needs_a_plan_and_a_count():
    assert coverage_pct(0, 1000) == 0.0
    assert coverage_pct(None, 1000) is None
    assert coverage_pct(0, 0) is None
    assert coverage_pct(900, None) is None


def test_the_interval_counts_trips_not_stop_events():
    """18,000 of 20,000 stop events on time is 90%; over 900 trips the 95%
    Wilson interval is about ±2 points, where 20,000 independent events
    would claim about ±0.4."""
    low, high = _on_time_interval(18_000, 20_000, 900)
    assert (low, high) == (87.8, 91.8)


def test_the_interval_rounds_outward_so_it_never_reads_narrower_than_computed():
    from pipeline.stats import wilson_interval

    low, high = _on_time_interval(450, 900, 45)
    exact_low, exact_high = wilson_interval(round(0.5 * 45), 45)
    assert low <= exact_low * 100 and high >= exact_high * 100
    assert high - low < (exact_high - exact_low) * 100 + 0.2


def test_no_interval_without_samples_or_a_trip_count():
    assert _on_time_interval(0, 0, 900) == (None, None)
    assert _on_time_interval(18_000, 20_000, None) == (None, None)
    assert _on_time_interval(18_000, 20_000, 0) == (None, None)


def _assess(**over):
    base = {
        "ingest_strategy": "static_join",
        "observed_trips": 900,
        "coverage_planned_trips": 1000,
        "on_time_count": 18_000,
        "samples": 20_000,
        "clamp_pct": 0.2,
    }
    return assess(**{**base, **over})


def test_assess_gives_every_figure_the_board_prints_and_the_verdict():
    a = _assess()
    assert (a.observed_trips, a.coverage_planned_trips, a.coverage_pct) == (900, 1000, 90.0)
    assert (a.ci_low, a.ci_high) == (87.8, 91.8)
    assert (a.comparable, a.comparable_reason) == (True, None)


@pytest.mark.parametrize("strategy", ["aomori_regex", None, "something_new"])
def test_a_strategy_whose_trips_cannot_be_matched_to_the_timetable_measures_nothing(strategy):
    """Its trip_ids aren't the timetable's, so its trip count is no count of
    the timetable's trips: no coverage, no interval, and no rank."""
    a = _assess(ingest_strategy=strategy)
    assert (a.observed_trips, a.coverage_planned_trips, a.coverage_pct) == (None, None, None)
    assert (a.ci_low, a.ci_high) == (None, None)
    assert (a.comparable, a.comparable_reason) == (False, "coverage_unknown")


def test_assess_without_samples_is_no_data_whatever_the_strategy():
    assert _assess(ingest_strategy="aomori_regex", samples=0, on_time_count=0).comparable_reason == "no_data"


def test_assess_with_a_plan_but_nothing_observed_is_coverage_unknown_only_without_a_plan():
    assert _assess(observed_trips=None, coverage_planned_trips=0).comparable_reason == "coverage_unknown"
    assert _assess(observed_trips=0, coverage_planned_trips=1000).comparable_reason == "coverage_low"


def test_clamp_share_is_clamped_over_raw_to_two_decimals():
    assert clamp_pct(3, 1000) == 0.3
    assert clamp_pct(1, 3) == 33.33


def test_clamp_share_without_raw_samples_is_unknown_not_zero():
    assert clamp_pct(0, 0) is None
    assert clamp_pct(0, 1000) == 0.0


def test_only_timetable_matched_strategies_have_countable_trips():
    assert trips_matchable("static_join")
    assert not trips_matchable("aomori_regex")
    assert not trips_matchable(None)
