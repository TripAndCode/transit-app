"""Pure-logic tests for the delay histogram (bucketing + percentile interp)."""

import pytest

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
    observation sits well clear of the bucket ``high_sec`` (60) itself falls
    in (``[60, 120)``) — that bucket is covered by
    ``test_count_in_range_inclusive_at_high_sec_bucket_edge`` below instead,
    since seeding it here would make this exact-count example approximate.
    """
    counts = _hist(*([-200] * 6 + [30] * 12 + [150] * 4))
    on_time = count_in_range(counts, None, 60)
    assert on_time == 18  # the -200s and 30s groups; the 150s group is late


def test_count_in_range_hand_computed_early_tolerance_60s():
    """early_tolerance_sec=60 (window low bound -60s) excludes departures
    earlier than that, unlike the legacy unbounded-early default. -60 is
    also a bucket edge (-60 - LO(-300) = 240 = 4*WIDTH), so this is exact,
    not merely bucket-approximate — a hand-countable example. The upper
    group is kept clear of the ``high_sec=60`` boundary bucket for the same
    reason as the test above."""
    counts = _hist(*([-200] * 3 + [-30] * 7 + [30] * 5 + [150] * 4))
    # Window [-60, 60]: excludes the -200s group (earlier than -60s tolerance
    # allows), includes -30s and 30s, excludes 150s (later than 60s tolerance).
    on_time = count_in_range(counts, -60, 60)
    assert on_time == 12


def test_count_in_range_inclusive_at_high_sec_bucket_edge():
    """A ``high_sec`` that lands exactly on a bucket's low edge (60 is the
    low edge of bucket ``[60, 120)``) must still count observations in that
    bucket — ``dep_delay == 60`` is on-time under legacy ``<= 60`` semantics,
    and must not be silently clipped to a zero-width, zero-count window."""
    counts = _hist(*([30] * 9 + [65] * 9))
    on_time = count_in_range(counts, None, 60)
    assert on_time > 0
    assert on_time == pytest.approx(9 + 9 * (1 / WIDTH))  # [60,120)'s 1s slice


def test_count_in_range_exclusive_at_low_sec_bucket_edge_for_late_count():
    """The mirror case for the late/severe direction: ``total -
    count_in_range(hist, None, 300)`` must not count ``dep_delay == 300`` as
    late (legacy semantics is ``dep_delay > 300``), i.e. bucket ``[300,
    360)`` must still be (almost) fully attributed to the on-time side."""
    counts = _hist(*([30] * 5 + [300] * 6))
    total = sum(counts)
    late = total - count_in_range(counts, None, 300)
    assert late < 6  # not "every observation at the 300 bucket counts late"
    assert late == pytest.approx(6 * (1 - 1 / WIDTH))  # only the >300s slice


def test_count_in_range_zero_width_window_is_not_always_zero():
    """low_sec == high_sec (e.g. both new tolerance params passed as 0) is a
    degenerate single-point query, not an always-empty one — it must still
    attribute the enclosing bucket's proportional single-point share instead
    of silently returning 0 regardless of data."""
    counts = _hist(*([0] * 12))
    assert count_in_range(counts, 0, 0) == pytest.approx(12 * (1 / WIDTH))


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
