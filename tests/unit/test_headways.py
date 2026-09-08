"""Pure-logic tests for headway reconstruction and high-frequency classification.

No DB fixtures needed -- `pipeline.headways` is pure Python (see that module's
docstring for why the reconstruction/classification logic is split out from
its ClickHouse/Postgres callers in `pipeline.analyze`).
"""

from pipeline.headways import (
    HIGH_FREQUENCY_HEADWAY_SEC,
    coefficient_of_variation,
    coefficient_of_variation_from_pooled,
    count_long_gaps,
    excess_wait_time_sec,
    headway_median_sec,
    is_high_frequency,
    long_gap_rate,
    mean_wait_from_pooled,
    mean_wait_sec,
    reconstruct_headways,
)


def test_reconstruct_headways_low_noise_fixture_matches_hand_computed():
    # A route with vehicles at 08:00:00, 08:08:03, 08:16:01, 08:23:58
    # (seconds-since-midnight) -- a low-noise, close-to-8-minute-headway
    # fixture. Hand-computed gaps: 483s, 478s, 477s.
    times = [8 * 3600, 8 * 3600 + 8 * 60 + 3, 8 * 3600 + 16 * 60 + 1, 8 * 3600 + 23 * 60 + 58]
    gaps = reconstruct_headways(times)
    assert gaps == [483, 478, 477]
    # Within a few seconds of the hand-computed ~480s (8 min) median.
    assert abs(headway_median_sec(times) - 480) <= 6


def test_reconstruct_headways_sorts_unordered_input():
    # Order of input must not matter -- reconstruction sorts internally.
    times = [100, 0, 300, 190]
    assert reconstruct_headways(times) == [100, 90, 110]


def test_reconstruct_headways_needs_at_least_two_events():
    assert reconstruct_headways([]) == []
    assert reconstruct_headways([42]) == []
    assert headway_median_sec([]) is None
    assert headway_median_sec([42]) is None


def test_reconstruct_headways_duplicate_timestamp_is_a_zero_gap():
    assert reconstruct_headways([100, 100]) == [0]


def test_headway_median_sec_odd_and_even_gap_counts():
    # 3 events -> 2 gaps (10, 20) -> median of an even-length list.
    assert headway_median_sec([0, 10, 30]) == 15
    # 4 events -> 3 gaps (10, 10, 10) -> exact median.
    assert headway_median_sec([0, 10, 20, 30]) == 10


def test_is_high_frequency_known_8_minute_route():
    # ~8-minute scheduled headway median -> high-frequency.
    eight_min_sec = 8 * 60
    assert is_high_frequency(eight_min_sec) is True


def test_is_high_frequency_known_30_minute_route():
    # 30-minute scheduled headway median -> not high-frequency.
    thirty_min_sec = 30 * 60
    assert is_high_frequency(thirty_min_sec) is False


def test_is_high_frequency_boundary_and_missing_data():
    assert is_high_frequency(HIGH_FREQUENCY_HEADWAY_SEC) is True  # inclusive boundary
    assert is_high_frequency(HIGH_FREQUENCY_HEADWAY_SEC + 1) is False
    assert is_high_frequency(None) is False  # no schedule data is never high-frequency


# ---------------------------------------------------------------------------
# Headway QUALITY metrics (item 94): Excess Waiting Time, coefficient of
# variation, long-gap rate. Two synthetic fixtures per the item's own
# verification criteria:
#   - EVEN: perfectly regular 8-minute (480s) actual headways, identical to
#     the scheduled headways -- EWT and CoV must both be (exactly) zero.
#   - BUNCHED: two vehicles 60s apart followed by a compensating ~900s gap
#     (same total as two scheduled 480s headways, so the MEAN is unchanged --
#     isolating the effect of variance/bunching alone) -- EWT must be
#     positive and the long-gap rate non-zero.
# ---------------------------------------------------------------------------

_EVEN_GAPS = [480.0, 480.0, 480.0]
_SCHEDULED_GAPS = [480.0, 480.0]
_BUNCHED_GAPS = [60.0, 900.0]  # sums to 960 == 2 * 480, same mean as _SCHEDULED_GAPS


def test_mean_wait_sec_even_spacing_is_half_the_headway():
    # A perfectly regular headway's mean wait reduces to the familiar
    # headway/2 (no length-biased-sampling penalty when there's no variance).
    assert mean_wait_sec(_EVEN_GAPS) == 240.0


def test_mean_wait_sec_empty_is_none():
    assert mean_wait_sec([]) is None


def test_coefficient_of_variation_even_spacing_is_zero():
    assert coefficient_of_variation(_EVEN_GAPS) == 0.0


def test_coefficient_of_variation_needs_at_least_two_gaps():
    assert coefficient_of_variation([480.0]) is None
    assert coefficient_of_variation([]) is None


def test_coefficient_of_variation_bunched_fixture_is_positive():
    assert coefficient_of_variation(_BUNCHED_GAPS) > 0


def test_excess_wait_time_even_fixture_is_zero():
    # Actual headways identical to scheduled -> EWT is exactly zero, not
    # merely close to it.
    assert excess_wait_time_sec(_EVEN_GAPS, _EVEN_GAPS) == 0.0


def test_excess_wait_time_bunched_fixture_is_positive():
    # Same total time as two scheduled headways (mean unchanged), but
    # bunched into an uneven pair -- length-biased sampling means a
    # passenger arriving at random is more likely to land in the long gap,
    # so the actual mean wait exceeds the scheduled one.
    ewt = excess_wait_time_sec(_BUNCHED_GAPS, _SCHEDULED_GAPS)
    assert ewt is not None
    assert ewt > 0


def test_excess_wait_time_missing_side_is_none():
    assert excess_wait_time_sec([], _SCHEDULED_GAPS) is None
    assert excess_wait_time_sec(_EVEN_GAPS, []) is None


def test_count_long_gaps_and_long_gap_rate_bunched_fixture():
    # threshold = 1.75 * 480 = 840 -- only the 900s gap qualifies.
    assert count_long_gaps(_BUNCHED_GAPS, 480.0) == 1
    assert long_gap_rate(_BUNCHED_GAPS, 480.0) == 0.5


def test_count_long_gaps_and_long_gap_rate_even_fixture_is_zero():
    assert count_long_gaps(_EVEN_GAPS, 480.0) == 0
    assert long_gap_rate(_EVEN_GAPS, 480.0) == 0.0


def test_long_gap_rate_none_without_a_scheduled_reference():
    assert long_gap_rate(_BUNCHED_GAPS, None) is None
    assert long_gap_rate(_BUNCHED_GAPS, 0.0) is None
    assert count_long_gaps(_BUNCHED_GAPS, None) == 0


def test_pooled_forms_agree_with_direct_forms_over_the_same_gaps():
    # mean_wait_from_pooled/coefficient_of_variation_from_pooled must be
    # bit-for-bit interchangeable with the direct-from-gaps forms when fed
    # the same population's sufficient statistics -- this is what lets
    # pipeline.reports.headway_quality pool multiple days of
    # agg_route_headway_daily via a plain SQL SUM instead of re-fetching
    # every raw gap.
    gaps = _EVEN_GAPS + _BUNCHED_GAPS
    n, s, ssq = len(gaps), sum(gaps), sum(g * g for g in gaps)
    assert mean_wait_from_pooled(n, s, ssq) == mean_wait_sec(gaps)
    assert coefficient_of_variation_from_pooled(n, s, ssq) == coefficient_of_variation(gaps)


def test_pooling_two_days_matches_pooling_their_concatenation():
    day1, day2 = _EVEN_GAPS, _BUNCHED_GAPS
    n1, s1, sq1 = len(day1), sum(day1), sum(g * g for g in day1)
    n2, s2, sq2 = len(day2), sum(day2), sum(g * g for g in day2)
    pooled_n, pooled_s, pooled_sq = n1 + n2, s1 + s2, sq1 + sq2

    combined = day1 + day2
    assert mean_wait_from_pooled(pooled_n, pooled_s, pooled_sq) == mean_wait_sec(combined)
    assert coefficient_of_variation_from_pooled(pooled_n, pooled_s, pooled_sq) == coefficient_of_variation(combined)


def test_mean_wait_from_pooled_no_samples_is_none():
    assert mean_wait_from_pooled(0, 0.0, 0.0) is None


def test_coefficient_of_variation_from_pooled_needs_two_samples_and_positive_mean():
    assert coefficient_of_variation_from_pooled(1, 480.0, 480.0 * 480.0) is None
    assert coefficient_of_variation_from_pooled(2, 0.0, 0.0) is None
