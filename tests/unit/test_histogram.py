"""Pure-logic tests for the delay histogram (bucketing + percentile interp)."""

from pipeline.histogram import HI, LO, N_BUCKETS, WIDTH, bucketize, count_in_range, percentile_from_hist


def test_bucketize_boundaries():
    assert bucketize(LO - 1) == 0  # underflow
    assert bucketize(LO) == 1  # first inner bin
    assert bucketize(LO + WIDTH - 1) == 1  # still first inner bin
    assert bucketize(LO + WIDTH) == 2  # next bin
    assert bucketize(HI - 1) == N_BUCKETS - 2  # last inner bin
    assert bucketize(HI) == N_BUCKETS - 1  # overflow
    assert bucketize(HI + 10_000) == N_BUCKETS - 1


def test_bucketize_zero_and_thresholds():
    # 0s lands in the [0, 60) bin; 60s in the next — relevant to on_time edge.
    assert bucketize(0) == bucketize(59)
    assert bucketize(60) == bucketize(0) + 1


def _hist(*delays: int) -> list[int]:
    counts = [0] * N_BUCKETS
    for d in delays:
        counts[bucketize(d)] += 1
    return counts


def test_percentile_empty_is_none():
    assert percentile_from_hist([0] * N_BUCKETS, 0.5) is None


def test_percentile_single_bucket_interpolates_within_bounds():
    # All 10 samples in [0, 60): the q-quantile lands inside that bin.
    counts = _hist(*([30] * 10))
    p50 = percentile_from_hist(counts, 0.5)
    assert 0 <= p50 <= 60


def test_percentile_split_distribution():
    # 10 @ [0,60) then 10 @ [600,660): p50 at the boundary, p90 deep in the 2nd.
    counts = _hist(*([30] * 10 + [630] * 10))
    p50 = percentile_from_hist(counts, 0.5)
    p90 = percentile_from_hist(counts, 0.9)
    assert p50 == 60.0  # rank 10 of 20 → top edge of first bin
    assert 600 <= p90 < 660  # rank 18 → inside the second populated bin


def test_percentile_monotonic_in_q():
    counts = _hist(*range(-200, 1700, 10))
    p10 = percentile_from_hist(counts, 0.1)
    p50 = percentile_from_hist(counts, 0.5)
    p90 = percentile_from_hist(counts, 0.9)
    assert p10 < p50 < p90


def test_count_in_range_unbounded_both_sides_is_total():
    counts = _hist(*([-30] * 7 + [30] * 5 + [900] * 3))
    assert count_in_range(counts, None, None) == 15


def test_count_in_range_legacy_60s_on_time_window_matches_exact_count():
    """early_tolerance_sec=None (unbounded early), late_tolerance_sec=60 —
    the legacy_60s preset's own on-time window — must exactly reproduce
    analyze()'s ``COUNT(*) FILTER (WHERE dep_delay <= 60)`` when every
    observation sits well inside its bucket (60 is a bucket edge: 60 -
    LO(-300) = 360 = 6*WIDTH, so no observation straddles it here)."""
    counts = _hist(*([-200] * 6 + [30] * 12 + [90] * 4))
    on_time = count_in_range(counts, None, 60)
    assert on_time == 18  # the -200s and 30s groups; the 90s group is late


def test_count_in_range_hand_computed_early_tolerance_60s():
    """early_tolerance_sec=60 (window low bound -60s) excludes departures
    earlier than that, unlike the legacy unbounded-early default. -60 is
    also a bucket edge (-60 - LO(-300) = 240 = 4*WIDTH), so this is exact,
    not merely bucket-approximate — a hand-countable example."""
    counts = _hist(*([-200] * 3 + [-30] * 7 + [30] * 5 + [90] * 4))
    # Window [-60, 60]: excludes the -200s group (earlier than -60s tolerance
    # allows), includes -30s and 30s, excludes 90s (later than 60s tolerance).
    on_time = count_in_range(counts, -60, 60)
    assert on_time == 12


def test_count_in_range_legacy_300s_late_count_matches_exact_count():
    """total - count_in_range(hist, None, 300) must exactly reproduce
    analyze()'s ``COUNT(*) FILTER (WHERE dep_delay > 300)`` when no
    observation sits exactly at the 300s edge."""
    counts = _hist(*([30] * 8 + [200] * 2 + [400] * 3))
    total = sum(counts)
    late = total - count_in_range(counts, None, 300)
    assert late == 3


def test_count_in_range_within_single_bucket_is_bounded_approximation():
    """A window that lands strictly inside one populated bucket (not on an
    edge) can only estimate a fraction of that bucket's count — bounded by
    the bucket's own total, matching this function's documented one-bucket
    error bound."""
    counts = _hist(*([10] * 20))  # all 20 in the [0, 60) bucket
    estimate = count_in_range(counts, None, 30)  # 30s is mid-bucket, not an edge
    assert 0 < estimate < 20
