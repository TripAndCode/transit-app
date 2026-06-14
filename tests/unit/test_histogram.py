"""Pure-logic tests for the delay histogram (bucketing + percentile interp)."""

from pipeline.histogram import HI, LO, N_BUCKETS, WIDTH, bucketize, percentile_from_hist


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
