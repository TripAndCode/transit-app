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


def n_buckets_for(*, lo: int = LO, hi: int = HI, width: int = WIDTH) -> int:
    """Return the bucket-array length for a given ``(lo, hi, width)`` bound
    triple — one underflow bucket, ``(hi - lo) // width`` inner bins, one
    overflow bucket. :data:`N_BUCKETS` is exactly ``n_buckets_for()`` (the
    default delay-histogram bounds); other distributions (e.g.
    ``pipeline.dwell_run``'s dwell/running-time histograms) call this with
    their own bounds instead of hardcoding a bucket count that could drift
    from the bounds actually used to build it.
    """
    return (hi - lo) // width + 2


def bucketize(delay_sec: int, *, lo: int = LO, hi: int = HI, width: int = WIDTH) -> int:
    """Return the histogram bucket index for *delay_sec*.

    ``lo``/``hi``/``width`` default to this module's own delay-histogram
    bounds; a caller bucketing a differently-scaled distribution (e.g. dwell
    or running time, seconds-at-a-stop rather than seconds-of-delay) passes
    its own bounds instead — the underflow/overflow/inner-bin shape is
    identical either way, only the edges move.
    """
    n_inner = (hi - lo) // width
    if delay_sec < lo:
        return 0
    if delay_sec >= hi:
        return n_inner + 1
    return 1 + (delay_sec - lo) // width


def _bucket_bounds(index: int, *, lo: int = LO, hi: int = HI, width: int = WIDTH) -> tuple[float, float]:
    """Return the [low, high) second bounds of a bucket for interpolation.

    The open-ended underflow/overflow buckets are given a single WIDTH so a
    percentile landing in them resolves to a finite, sensible edge value
    rather than ``-inf``/``+inf``.
    """
    n_inner = (hi - lo) // width
    if index == 0:
        return (lo - width, lo)
    if index == n_inner + 1:
        return (hi, hi + width)
    low = lo + (index - 1) * width
    return (low, low + width)


def percentile_from_hist(
    counts: list[int], q: float, *, lo: int = LO, hi: int = HI, width: int = WIDTH
) -> float | None:
    """Interpolate the *q* quantile (0..1) in seconds from merged bucket *counts*.

    Linear interpolation within the bucket that contains the target rank — the
    standard histogram-percentile estimate. Returns ``None`` for an empty
    histogram. ``counts`` must have length ``n_buckets_for(lo=lo, hi=hi,
    width=width)`` (:data:`N_BUCKETS` for the default bounds).
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
            low, high = _bucket_bounds(index, lo=lo, hi=hi, width=width)
            # Fraction into this bucket where the target rank falls.
            frac = (target - cumulative) / c
            return low + frac * (high - low)
        cumulative += c
    # Floating-point slack (target == total) falls through — return the top
    # edge of the last POPULATED bucket, not the fixed overflow edge.
    return _bucket_bounds(last_populated, lo=lo, hi=hi, width=width)[1]


def count_in_range(
    counts: list[int],
    low_sec: float | None,
    high_sec: float | None,
    *,
    lo: int = LO,
    hi: int = HI,
    width: int = WIDTH,
) -> float:
    """Estimate the count of observations with ``low_sec <= delay <= high_sec``
    from merged bucket *counts*, for an on-time/late tolerance chosen at query
    time instead of the fixed threshold analyze() bakes into the exact
    ``on_time_count``/``late5_count`` columns.

    Each bucket's count is split proportionally to how much of its
    ``[low, high)`` span falls inside the query window, i.e. delay is assumed
    uniform within a bucket -- the same assumption
    :func:`percentile_from_hist`'s interpolation makes for the inverse
    (quantile -> value) direction. ``dep_delay`` is whole seconds, so an
    inclusive ``high_sec`` is compared as ``high_sec + 1`` against each
    bucket's half-open ``[low, high)`` bound -- matching :func:`bucketize`'s
    own half-open convention, so an observation exactly equal to ``high_sec``
    (or ``low_sec``) is attributed to the correct side instead of the whole
    bucket it lands in being silently clipped to zero width. Every bucket
    strictly below/above a bound is counted exactly; the one bucket a bound
    falls inside still carries the usual one-bucket (``WIDTH`` seconds)
    uniform-distribution error, same bound as that function's own documented
    approximation. ``None`` on either side means unbounded on that side.
    ``counts`` must have length :data:`N_BUCKETS`.
    """
    total = 0.0
    for index, c in enumerate(counts):
        if c == 0:
            continue
        low, high = _bucket_bounds(index, lo=lo, hi=hi, width=width)
        lo_clip = low if low_sec is None else max(low, low_sec)
        hi_clip = high if high_sec is None else min(high, high_sec + 1)
        if hi_clip <= lo_clip:
            continue
        total += c * (hi_clip - lo_clip) / (high - low)
    return total


def bucket_case_sql(column: str, *, lo: int = LO, hi: int = HI, width: int = WIDTH) -> str:
    """Return a SQL ``CASE`` expression bucketing *column* exactly like
    :func:`bucketize` (same bounds/edge conventions).

    ``NULL`` propagates to ``NULL`` (not bucket 0), so a caller composing a
    ``COUNT(*) FILTER (WHERE bucket_col = i)`` histogram naturally excludes
    rows where *column* itself is unmeasured, rather than miscounting them as
    underflow. ``analyze()``'s own delay-histogram ``_BUCKET_EXPR`` predates
    this helper and doesn't need the NULL guard (``dep_delay`` is already
    filtered non-NULL upstream there); new bucketed columns that CAN be NULL
    per row (e.g. ``pipeline.dwell_run``'s dwell/running seconds) should use
    this helper instead of hand-rolling the same CASE shape.
    """
    n_inner = (hi - lo) // width
    return (
        f"CASE WHEN {column} IS NULL THEN NULL "
        f"WHEN {column} < {lo} THEN 0 "
        f"WHEN {column} >= {hi} THEN {n_inner + 1} "
        f"ELSE 1 + ({column} - ({lo})) / {width} END"
    )


def hist_array_sql(bucket_col: str, *, lo: int = LO, hi: int = HI, width: int = WIDTH) -> str:
    """Return a fixed-length ``ARRAY[...]`` SQL expression of per-bucket
    ``COUNT(*) FILTER (WHERE bucket_col = i)`` counts, one per bucket index —
    the aggregate-time counterpart to :func:`bucket_case_sql`. A row whose
    *bucket_col* is NULL (see that function's NULL-propagation note) matches
    no ``= i`` filter and is silently excluded from every bucket, not
    miscounted into one.
    """
    n = n_buckets_for(lo=lo, hi=hi, width=width)
    return "ARRAY[" + ", ".join(f"COUNT(*) FILTER (WHERE {bucket_col} = {i})" for i in range(n)) + "]::int[]"
