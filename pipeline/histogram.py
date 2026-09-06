"""Fixed-width delay histogram: bucketing + percentile interpolation.

Powers the range-scoped report aggregates (``agg_route_daily_dist``). Exact
statistics (avg, on-time%, worst-5min) compose trivially across days by summing
counts; percentiles do **not**, so we store a per-day/route delay histogram and
interpolate p50/p90 from the merged buckets over the requested range. The
approximation error is bounded by ``WIDTH`` (one bucket), acceptable for ranking.

Bucketing here (analyze write path) and interpolation here (report read path)
share the same edges so the two never drift — change ``LO``/``HI``/``WIDTH`` in
one place and both sides move together.

:func:`count_in_range` reads the same merged buckets to estimate an on-time
window or a "later than X" count for a caller-chosen tolerance, so a report
can answer a non-default on-time/late definition at query time without a
raw-updates scan or an analyze() re-aggregation -- see
``pipeline.reports.rankings.compute_on_time``/``compute_worst_5min``.
"""

# Inner bins span [LO, HI) in WIDTH-second steps; bucket 0 catches early/negative
# delays below LO, the final bucket catches everything at or beyond HI. dep_delay
# is seconds (can be negative when a bus departs early).
LO = -300
HI = 1800
WIDTH = 60
_N_INNER = (HI - LO) // WIDTH  # 35 inner bins
# 0 = underflow, 1.._N_INNER = inner, _N_INNER+1 = overflow.
N_BUCKETS = _N_INNER + 2

# The write path (pipeline/analyze.py) bakes these two thresholds into exact
# per-route scalar columns at analyze time (agg_route_daily_dist's
# on_time_count/late5_count, agg_route_stats's on_time_pct/late5_pct/
# late_5min_plus) -- the "legacy_60s" preset. Both that write path and the
# read path (pipeline/reports/rankings.py) import these two constants
# instead of repeating the literals, so the exact precomputed fast-path
# columns and this default preset's own values can never drift apart -- the
# same rationale LO/HI/WIDTH above already apply to bucket edges.
LEGACY_ON_TIME_LATE_TOLERANCE_SEC = 60
LEGACY_SEVERE_LATE_TOLERANCE_SEC = 300
LEGACY_PRESET_NAME = "legacy_60s"


def bucketize(delay_sec: int) -> int:
    """Return the histogram bucket index for *delay_sec*."""
    if delay_sec < LO:
        return 0
    if delay_sec >= HI:
        return _N_INNER + 1
    return 1 + (delay_sec - LO) // WIDTH


def _bucket_bounds(index: int) -> tuple[float, float]:
    """Return the [low, high) second bounds of a bucket for interpolation.

    The open-ended underflow/overflow buckets are given a single WIDTH so a
    percentile landing in them resolves to a finite, sensible edge value
    rather than ``-inf``/``+inf``.
    """
    if index == 0:
        return (LO - WIDTH, LO)
    if index == _N_INNER + 1:
        return (HI, HI + WIDTH)
    low = LO + (index - 1) * WIDTH
    return (low, low + WIDTH)


def percentile_from_hist(counts: list[int], q: float) -> float | None:
    """Interpolate the *q* quantile (0..1) in seconds from merged bucket *counts*.

    Linear interpolation within the bucket that contains the target rank — the
    standard histogram-percentile estimate. Returns ``None`` for an empty
    histogram. ``counts`` must have length :data:`N_BUCKETS`.
    """
    total = sum(counts)
    if total == 0:
        return None
    target = q * total
    cumulative = 0
    last_populated = 0
    for index, c in enumerate(counts):
        if c == 0:
            continue
        last_populated = index
        if cumulative + c >= target:
            low, high = _bucket_bounds(index)
            # Fraction into this bucket where the target rank falls.
            frac = (target - cumulative) / c
            return low + frac * (high - low)
        cumulative += c
    # Floating-point slack (target == total) falls through — return the top
    # edge of the last POPULATED bucket, not the fixed overflow edge.
    return _bucket_bounds(last_populated)[1]


def count_in_range(counts: list[int], low_sec: float | None, high_sec: float | None) -> float:
    """Estimate the count of observations with ``low_sec <= delay <= high_sec``
    from merged bucket *counts*, for an on-time/late tolerance chosen at query
    time instead of the fixed threshold analyze() bakes into the exact
    ``on_time_count``/``late5_count`` columns.

    Each bucket's count is split proportionally to how much of its
    ``[low, high)`` span falls inside ``[low_sec, high_sec]``, i.e. delay is
    assumed uniform within a bucket -- the same assumption
    :func:`percentile_from_hist`'s interpolation makes for the inverse
    (quantile -> value) direction. The result is therefore exact only when
    both bounds land on a bucket edge (a multiple of ``WIDTH`` away from
    ``LO``); otherwise the error is bounded by one bucket (``WIDTH`` seconds)
    of real observations near either edge, same bound as that function's own
    documented approximation. ``None`` on either side means unbounded on
    that side. ``counts`` must have length :data:`N_BUCKETS`.
    """
    total = 0.0
    for index, c in enumerate(counts):
        if c == 0:
            continue
        low, high = _bucket_bounds(index)
        lo_clip = low if low_sec is None else max(low, low_sec)
        hi_clip = high if high_sec is None else min(high, high_sec)
        if hi_clip <= lo_clip:
            continue
        total += c * (hi_clip - lo_clip) / (high - low)
    return total
