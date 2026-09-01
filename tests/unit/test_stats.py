"""Pure-logic tests for pipeline/stats.py (Wilson interval + confidence flag)."""

import math

import pytest

from pipeline.stats import annotate_on_time_pct_confidence, pct_is_uncertain, wilson_interval


def test_wilson_interval_matches_known_reference():
    # p=0.5, n=100, 95% CI ~ (0.404, 0.596) -- a widely-cited reference point.
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3)
    assert high == pytest.approx(0.5962, abs=1e-3)


def test_wilson_interval_narrows_with_more_samples_at_same_proportion():
    low_small, high_small = wilson_interval(10, 100)  # 10%
    low_big, high_big = wilson_interval(100, 1000)  # same 10%, 10x the n
    assert (high_big - low_big) < (high_small - low_small)


def test_wilson_interval_bounds_stay_within_unit_range():
    for successes, n in [(0, 1), (1, 1), (0, 50), (50, 50), (25, 50)]:
        low, high = wilson_interval(successes, n)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_zero_n_is_maximally_uninformative():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_rejects_out_of_range_successes():
    with pytest.raises(ValueError):
        wilson_interval(11, 10)
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)


def test_pct_is_uncertain_true_for_thin_sample():
    # 20/25 = 80% on a small n: wide interval, should read as uncertain.
    assert pct_is_uncertain(20, 25) is True


def test_pct_is_uncertain_false_for_large_confident_sample():
    # 270/300 = 90% on a comfortably large n: interval narrows under the
    # default 5pp half-width cutoff.
    assert pct_is_uncertain(270, 300) is False


def test_pct_is_uncertain_zero_samples_is_always_uncertain():
    assert pct_is_uncertain(0, 0) is True


def test_pct_is_uncertain_boundary_uses_strict_greater_than():
    # A half-width exactly AT the cutoff must not be flagged (only strictly
    # exceeding it should be) -- construct one via the cutoff itself.
    low, high = wilson_interval(270, 300)
    half_width_pp = (high - low) * 100 / 2
    assert pct_is_uncertain(270, 300, max_half_width_pp=half_width_pp) is False
    assert pct_is_uncertain(270, 300, max_half_width_pp=half_width_pp - 1e-9) is True


def test_annotate_on_time_pct_confidence_appends_bool_without_mutating_input():
    rows = [("14081", "平日", 80.0, 0.5, 25), ("14082", "平日", 90.0, 0.5, 300)]
    out = annotate_on_time_pct_confidence(rows)
    assert len(out) == 2
    assert out[0][:5] == rows[0]
    assert out[0][5] is True
    assert out[1][:5] == rows[1]
    assert out[1][5] is False
    # Original rows list/tuples must be untouched (display-layer only).
    assert rows == [("14081", "平日", 80.0, 0.5, 25), ("14082", "平日", 90.0, 0.5, 300)]


def test_annotate_on_time_pct_confidence_empty_list():
    assert annotate_on_time_pct_confidence([]) == []


def test_wilson_interval_symmetry_around_half():
    """The interval for p and 1-p (same n) must be mirror images."""
    low_a, high_a = wilson_interval(30, 100)
    low_b, high_b = wilson_interval(70, 100)
    assert low_b == pytest.approx(1 - high_a, abs=1e-9)
    assert high_b == pytest.approx(1 - low_a, abs=1e-9)


def test_wilson_interval_matches_hand_derivation_for_10pct():
    # Hand-derived per the Wilson score formula for successes=10, n=100 (see
    # pipeline/stats.py's docstring for the same z=1.959964).
    z = 1.959964
    n = 100
    phat = 0.10
    z2 = z * z
    denom = 1 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    expected_low = (center - margin) / denom
    expected_high = (center + margin) / denom
    low, high = wilson_interval(10, 100)
    assert low == pytest.approx(expected_low)
    assert high == pytest.approx(expected_high)
