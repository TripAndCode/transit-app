"""Pure-logic tests for the delay histogram (bucketing + percentile interp)."""

import pytest

from pipeline.histogram import (
    HI,
    LO,
    N_BUCKETS,
    WIDTH,
    bucketize,
    count_early,
    count_in_range,
    count_on_time,
    early_count_sql,
    early_edge_buckets,
    exact_window,
    on_time_from_columns,
    percentile_from_hist,
    within_histogram,
)


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
    assert p50 is not None
    assert 0 <= p50 <= 60


def test_percentile_split_distribution():
    # 10 @ [0,60) then 10 @ [600,660): p50 at the boundary, p90 deep in the 2nd.
    counts = _hist(*([30] * 10 + [630] * 10))
    p50 = percentile_from_hist(counts, 0.5)
    p90 = percentile_from_hist(counts, 0.9)
    assert p50 == 60.0  # rank 10 of 20 → top edge of first bin
    assert p90 is not None
    assert 600 <= p90 < 660  # rank 18 → inside the second populated bin


def test_percentile_monotonic_in_q():
    counts = _hist(*range(-200, 1700, 10))
    p10 = percentile_from_hist(counts, 0.1)
    p50 = percentile_from_hist(counts, 0.5)
    p90 = percentile_from_hist(counts, 0.9)
    assert p10 is not None and p50 is not None and p90 is not None
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


_EARLY_EDGES = [-400, -301, -300, -299, -121, -120, -61, -60, -59, -1, 0, 59, 60, 400]


@pytest.mark.parametrize("early_sec", [0, 60, 120, 300])
def test_count_early_counts_departures_strictly_more_than_the_tolerance_early(early_sec):
    """More than T early is ``dep_delay < -T``: a departure exactly T early
    is not early. Every T that is a whole number of buckets inside the
    histogram's early side puts -T on a bucket edge, so the count is exact."""
    counts = _hist(*_EARLY_EDGES)
    assert count_early(counts, early_sec) == sum(d < -early_sec for d in _EARLY_EDGES)


def test_count_early_at_one_minute_leaves_out_a_departure_exactly_one_minute_early():
    assert count_early(_hist(-61, -60, -59), 60) == 1


@pytest.mark.parametrize("early_sec", [301, 330, 360, 600])
def test_count_early_refuses_a_threshold_below_the_histograms_floor(early_sec):
    """The underflow bucket is open below LO, so no share of it can be
    attributed to either side of a threshold there; such a count comes from
    the live scan instead."""
    with pytest.raises(ValueError):
        count_early(_hist(*_EARLY_EDGES), early_sec)


@pytest.mark.parametrize(
    ("early", "late", "inside"),
    [
        (None, None, True),
        (0, 60, True),
        (300, 1799, True),
        (301, None, False),
        (360, None, False),
        (600, 60, False),
        (None, 1800, False),
        (None, 2400, False),
    ],
)
def test_a_tolerance_lies_inside_the_histogram_only_between_its_open_end_buckets(early, late, inside):
    assert within_histogram(early, late) is inside


@pytest.mark.parametrize(("early_sec", "buckets"), [(0, 6), (60, 5), (120, 4), (300, 1)])
def test_the_leading_buckets_wholly_below_an_edge_hold_the_exact_early_count(early_sec, buckets):
    counts = _hist(*_EARLY_EDGES)
    assert early_edge_buckets(early_sec) == buckets
    assert sum(counts[:buckets]) == count_early(counts, early_sec) == sum(d < -early_sec for d in _EARLY_EDGES)


@pytest.mark.parametrize("early_sec", [30, 90, 330, 360])
def test_a_threshold_off_the_edges_has_no_exact_bucket_sum(early_sec):
    assert early_edge_buckets(early_sec) is None
    with pytest.raises(ValueError):
        early_count_sql(early_sec)


def test_the_early_count_column_sums_the_leading_buckets_by_postgres_index():
    assert early_count_sql(60) == "SUM(hist[1] + hist[2] + hist[3] + hist[4] + hist[5])"
    assert early_count_sql(300) == "SUM(hist[1])"


# Whole-minute readings on every edge an on-time window can have: exactly a
# minute early, exactly one and five minutes late, and one second past each.
_WINDOW_EDGES = [-400, -301, -300, -120, -61, -60, -59, 0, 30, 60, 61, 120, 299, 300, 301, 400]


def _exact_columns(delays: list[int]) -> dict:
    """agg_route_daily_dist's exact columns for *delays*: the legacy on-time
    count (at most 60 s late) and the over-five-minutes count."""
    return {
        "samples": len(delays),
        "on_time_count": sum(d <= 60 for d in delays),
        "late5_count": sum(d > 300 for d in delays),
    }


@pytest.mark.parametrize(
    ("early", "late"),
    [(None, 60), (60, 60), (None, 300), (60, 300), (0, 300), (300, 300), (120, 60)],
)
def test_count_on_time_is_exact_where_the_exact_columns_and_a_bucket_edge_bound_the_window(early, late):
    """A late side at one or five minutes reads the exact columns, and an
    early side on a bucket edge subtracts an exact early count, so readings
    piled on either edge are counted exactly."""
    low = -(early if early is not None else 10_000)
    expected = sum(low <= d <= late for d in _WINDOW_EDGES)
    assert count_on_time(_hist(*_WINDOW_EDGES), early, late, **_exact_columns(_WINDOW_EDGES)) == expected


@pytest.mark.parametrize(("early", "late"), [(None, 120), (60, 180), (None, 0)])
def test_count_on_time_estimates_from_the_histogram_where_no_exact_column_applies(early, late):
    counts = _hist(*_WINDOW_EDGES)
    estimate = count_in_range(counts, None if early is None else -early, late)
    assert count_on_time(counts, early, late, **_exact_columns(_WINDOW_EDGES)) == estimate


@pytest.mark.parametrize(("early", "late"), [(90, 300), (30, 60)])
def test_count_on_time_keeps_an_exact_late_edge_under_an_early_tolerance_off_the_edges(early, late):
    """Only the early side is estimated: the departures at most *late* late
    still come from the exact column."""
    counts = _hist(*_WINDOW_EDGES)
    columns = _exact_columns(_WINDOW_EDGES)
    at_most_late = sum(d <= late for d in _WINDOW_EDGES)
    expected = at_most_late - count_in_range(counts, None, -early - 1)
    assert count_on_time(counts, early, late, **columns) == pytest.approx(expected)


@pytest.mark.parametrize(("early", "late"), [(360, 60), (None, 1800), (None, 2400)])
def test_count_on_time_refuses_a_window_past_the_histograms_open_end_buckets(early, late):
    with pytest.raises(ValueError):
        count_on_time(_hist(*_WINDOW_EDGES), early, late, **_exact_columns(_WINDOW_EDGES))


@pytest.mark.parametrize(
    ("early", "late", "exact"),
    [
        (None, 60, True),
        (None, 300, True),
        (60, 300, True),
        (0, 60, True),
        (300, 300, True),
        (90, 300, False),
        (None, 120, False),
        (60, 180, False),
        (360, 60, False),
    ],
)
def test_the_exact_columns_answer_a_window_and_agree_with_the_histogram(early, late, exact):
    """The query that sums leading buckets and the merged-histogram path give
    one figure wherever the exact columns can answer, so the cheaper read can
    never change a number."""
    columns = _exact_columns(_WINDOW_EDGES)
    assert exact_window(early, late) is exact
    if not exact:
        with pytest.raises(ValueError):
            on_time_from_columns(early, late, early_count=0, **columns)
        return
    counts = _hist(*_WINDOW_EDGES)
    early_count = 0 if early is None else sum(counts[: early_edge_buckets(early)])
    low = -(early if early is not None else 10_000)
    truth = sum(low <= d <= late for d in _WINDOW_EDGES)
    from_columns = on_time_from_columns(early, late, early_count=early_count, **columns)
    assert from_columns == count_on_time(counts, early, late, **columns) == truth


def test_the_default_early_departure_threshold_sits_on_a_bucket_edge():
    """With early departures on time, the route table and the council report
    count the rate at this threshold from the leading buckets alone; off an
    edge, their exact windows would have no exact early count to read."""
    from pipeline.reports.rankings import DEFAULT_EARLY_DEPARTURE_SEC

    assert early_edge_buckets(DEFAULT_EARLY_DEPARTURE_SEC) is not None
