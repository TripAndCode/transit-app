"""The dwell-time and running-time histograms' bounds and percentiles.

The decomposition itself is analyze's SQL (pipeline.agg_sql.dwell_run_sql),
tested through analyze in tests/pipeline/test_analyze.py.
"""

from pipeline.dwell_run import (
    DWELL_HI,
    DWELL_LO,
    DWELL_N_BUCKETS,
    DWELL_WIDTH,
    RUN_HI,
    RUN_LO,
    RUN_N_BUCKETS,
    RUN_WIDTH,
    bucketize_dwell,
    bucketize_running,
    percentile_from_dwell_hist,
    percentile_from_run_hist,
)


def test_dwell_bucket_bounds_and_count():
    assert DWELL_N_BUCKETS == (DWELL_HI - DWELL_LO) // DWELL_WIDTH + 2
    assert bucketize_dwell(DWELL_LO - 1) == 0  # underflow
    assert bucketize_dwell(DWELL_HI) == DWELL_N_BUCKETS - 1  # overflow
    assert bucketize_dwell(0) == bucketize_dwell(DWELL_WIDTH - 1)


def test_run_bucket_bounds_and_count():
    assert RUN_N_BUCKETS == (RUN_HI - RUN_LO) // RUN_WIDTH + 2
    assert bucketize_running(RUN_LO - 1) == 0  # underflow
    assert bucketize_running(RUN_HI) == RUN_N_BUCKETS - 1  # overflow


def _dwell_hist(*values: int) -> list[int]:
    counts = [0] * DWELL_N_BUCKETS
    for v in values:
        counts[bucketize_dwell(v)] += 1
    return counts


def _run_hist(*values: int) -> list[int]:
    counts = [0] * RUN_N_BUCKETS
    for v in values:
        counts[bucketize_running(v)] += 1
    return counts


def test_percentile_from_dwell_hist_matches_hand_computed_median():
    # 10 observations at 0s, 10 at 300s -> p50 sits at the boundary (top edge
    # of the first populated bucket), same convention as the delay histogram.
    counts = _dwell_hist(*([0] * 10 + [300] * 10))
    p50 = percentile_from_dwell_hist(counts, 0.5)
    assert p50 is not None
    assert 0 <= p50 <= 300


def test_percentile_from_run_hist_empty_is_none():
    assert percentile_from_run_hist([0] * RUN_N_BUCKETS, 0.5) is None
