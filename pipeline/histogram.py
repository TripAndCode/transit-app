"""Fixed-width delay histogram: bucketing + percentile interpolation.

Powers the range-scoped report aggregates (``agg_route_daily_dist``). Exact
statistics (avg, on-time%, worst-5min) compose trivially across days by summing
counts; percentiles do **not**, so we store a per-day/route delay histogram and
interpolate p50/p90 from the merged buckets over the requested range. The
approximation error is bounded by ``WIDTH`` (one bucket), acceptable for ranking.

Bucketing (the write path, ``pipeline.ch_aggregates``'s ``_route_daily_dist``)
and interpolation here (the report read path) share the same edges so the two
never drift — change ``LO``/``HI``/``WIDTH`` in one place and both sides move
together.

:func:`count_in_range` reads the same merged buckets to estimate an on-time
window or a "later than X" count for a caller-chosen tolerance, so a report
can answer a non-default on-time/late definition at query time without a
raw-updates scan or an analyze() re-aggregation -- see
``pipeline.reports.rankings.compute_on_time``/``compute_worst_5min``.
"""

from collections.abc import Callable

# Inner bins span [LO, HI) in WIDTH-second steps; bucket 0 catches early/negative
# delays below LO, the final bucket catches everything at or beyond HI. dep_delay
# is seconds (can be negative when a bus departs early).
LO = -300
HI = 1800
WIDTH = 60
_N_INNER = (HI - LO) // WIDTH  # 35 inner bins
# 0 = underflow, 1.._N_INNER = inner, _N_INNER+1 = overflow.
N_BUCKETS = _N_INNER + 2

# The write paths bake these two thresholds into exact per-route scalar
# columns at analyze time (agg_route_daily_dist's on_time_count/late5_count in
# pipeline/ch_aggregates.py, agg_route_stats's on_time_pct/late5_pct/
# late_5min_plus, also in pipeline/ch_aggregates.py): the "legacy_60s" preset's
# late edge and the five-minute edge the industry preset shares. Both write
# paths and the read paths import these constants instead of repeating the
# literals, so the exact precomputed columns and the windows that read them
# can never drift apart -- the same rationale LO/HI/WIDTH above already apply
# to bucket edges.
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


def within_histogram(early_tolerance_sec: int | None, late_tolerance_sec: int | None) -> bool:
    """Whether every bound of an on-time window lies where the histogram can
    split a count: no more than ``-LO`` seconds early and less than ``HI``
    late. The end buckets are open, so no share of them belongs to either side
    of a bound inside them; a window reaching there needs the observations
    themselves."""
    early_inside = early_tolerance_sec is None or early_tolerance_sec <= -LO
    late_inside = late_tolerance_sec is None or late_tolerance_sec < HI
    return early_inside and late_inside


def early_edge_buckets(early_sec: int) -> int | None:
    """How many leading buckets lie wholly below ``-early_sec`` when
    ``-early_sec`` is a bucket edge, so that their sum is the exact count of
    departures more than *early_sec* early; ``None`` when it is not an edge.
    The one rule for when an early count is exact, which both
    :func:`early_count_sql` and :func:`exact_window` read."""
    if not 0 <= early_sec <= -LO or (-early_sec - LO) % WIDTH:
        return None
    return (-early_sec - LO) // WIDTH + 1


def _early_terms_sql(early_sec: int) -> str:
    """The ``hist`` buckets (Postgres arrays count from 1) wholly below
    ``-early_sec``, added up within one row. Raises ``ValueError`` when
    ``-early_sec`` is not a bucket edge."""
    buckets = early_edge_buckets(early_sec)
    if buckets is None:
        raise ValueError(f"-{early_sec} s is not a histogram bucket edge")
    return " + ".join(f"hist[{i}]" for i in range(1, buckets + 1))


def early_count_sql(early_sec: int) -> str:
    """A SQL aggregate summing a ``hist`` column's leading buckets wholly
    below ``-early_sec``: the exact count of departures more than
    *early_sec* early, without merging histograms. Raises ``ValueError``
    when ``-early_sec`` is not a bucket edge."""
    return f"SUM({_early_terms_sql(early_sec)})"


def count_early(counts: list[int], early_sec: int) -> float:
    """Estimate how many observations in merged delay-histogram *counts*
    departed more than *early_sec* seconds early, ``dep_delay < -early_sec``:
    a departure exactly *early_sec* early is not counted. ``dep_delay`` is
    whole seconds, so that is :func:`count_in_range` up to ``-early_sec - 1``
    inclusive. The count is exact whenever ``-early_sec`` is a bucket edge
    (:func:`early_edge_buckets`). Raises ``ValueError`` below the histogram's
    floor, where the open underflow bucket cannot be split.
    """
    if not within_histogram(early_sec, None):
        raise ValueError(f"{early_sec} s early lies below the histogram's floor")
    return count_in_range(counts, None, -early_sec - 1)


# The late tolerances analyze() bakes an exact count for, each with how the
# count of departures at most that late reads off the exact columns
# (samples, on_time_count, late5_count), in Python and as SQL over one
# agg_route_daily_dist row. The one table _at_most_late, on_time_count_sql and
# exact_window read, so the gate can never approve a window a reader refuses.
_EXACT_LATE: dict[int, tuple[Callable[[int, int, int], int], str]] = {
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC: (
        lambda samples, on_time_count, late5_count: on_time_count,
        "on_time_count",
    ),
    LEGACY_SEVERE_LATE_TOLERANCE_SEC: (
        lambda samples, on_time_count, late5_count: samples - late5_count,
        "(samples - late5_count)",
    ),
}


def _at_most_late(late_tolerance_sec: int, samples: int, on_time_count: int, late5_count: int) -> int | None:
    """The exact count of departures at most *late_tolerance_sec* late, where
    analyze() bakes one (:data:`_EXACT_LATE`)."""
    exact = _EXACT_LATE.get(late_tolerance_sec)
    return None if exact is None else exact[0](samples, on_time_count, late5_count)


def exact_window(early_tolerance_sec: int | None, late_tolerance_sec: int) -> bool:
    """Whether the exact columns answer the on-time window with no merged
    histogram: a late tolerance of one or five minutes, and no early tolerance
    or one on a bucket edge (:func:`early_edge_buckets`). Such a window, and
    the early-departure count at its threshold, are read with
    :func:`early_count_sql` and :func:`on_time_from_columns`."""
    return late_tolerance_sec in _EXACT_LATE and (
        early_tolerance_sec is None or early_edge_buckets(early_tolerance_sec) is not None
    )


def on_time_from_columns(
    early_tolerance_sec: int | None,
    late_tolerance_sec: int,
    *,
    samples: int,
    on_time_count: int,
    late5_count: int,
    early_count: int,
) -> int:
    """The exact on-time count of an :func:`exact_window`, from the exact
    columns and *early_count*, the departures more than
    *early_tolerance_sec* early that :func:`early_count_sql` sums (unused
    without an early tolerance). It equals :func:`count_on_time` over the
    same departures. Raises ``ValueError`` for any other window."""
    at_most_late = _at_most_late(late_tolerance_sec, samples, on_time_count, late5_count)
    if at_most_late is None or not exact_window(early_tolerance_sec, late_tolerance_sec):
        raise ValueError(f"({early_tolerance_sec}, {late_tolerance_sec}) is not an exact window")
    return at_most_late if early_tolerance_sec is None else at_most_late - early_count


def on_time_count_sql(early_tolerance_sec: int | None, late_tolerance_sec: int) -> str:
    """A SQL expression over one agg_route_daily_dist row: the exact on-time
    count of an :func:`exact_window`, as :func:`on_time_from_columns` reads
    it, so a caller can weight or sum it per row without merging histograms.
    Raises ``ValueError`` for any other window."""
    if not exact_window(early_tolerance_sec, late_tolerance_sec):
        raise ValueError(f"({early_tolerance_sec}, {late_tolerance_sec}) is not an exact window")
    at_most_late = _EXACT_LATE[late_tolerance_sec][1]
    if early_tolerance_sec is None:
        return at_most_late
    return f"({at_most_late} - ({_early_terms_sql(early_tolerance_sec)}))"


def count_on_time(
    counts: list[int],
    early_tolerance_sec: int | None,
    late_tolerance_sec: int,
    *,
    samples: int,
    on_time_count: int,
    late5_count: int,
) -> float:
    """The departures with ``-early_tolerance_sec <= dep_delay <=
    late_tolerance_sec`` (no early bound when it is ``None``), from merged
    histogram *counts* and the exact columns analyze() bakes beside it over
    the same departures: *samples*, *on_time_count* (at most
    ``LEGACY_ON_TIME_LATE_TOLERANCE_SEC`` late) and *late5_count* (more than
    ``LEGACY_SEVERE_LATE_TOLERANCE_SEC`` late).

    Feeds report many delays as whole minutes, which pile up on a bucket's
    lower edge, where the uniform estimate counts almost none of them. So a
    late tolerance of one or five minutes reads its exact column, less the
    departures earlier than the early tolerance (:func:`count_early`, exact
    on a bucket edge). Any other late tolerance is :func:`count_in_range`'s
    estimate. Raises ``ValueError`` for a window past the histogram's open
    end buckets (:func:`within_histogram`).
    """
    if not within_histogram(early_tolerance_sec, late_tolerance_sec):
        raise ValueError(f"({early_tolerance_sec}, {late_tolerance_sec}) reaches past the histogram")
    at_most_late = _at_most_late(late_tolerance_sec, samples, on_time_count, late5_count)
    if at_most_late is None:
        low_sec = None if early_tolerance_sec is None else -early_tolerance_sec
        return count_in_range(counts, low_sec, late_tolerance_sec)
    if early_tolerance_sec is None:
        return at_most_late
    return at_most_late - count_early(counts, early_tolerance_sec)


def bucket_case_sql(column: str, *, lo: int = LO, hi: int = HI, width: int = WIDTH) -> str:
    """Return a SQL ``CASE`` expression bucketing *column* exactly like
    :func:`bucketize` (same bounds/edge conventions).

    ``NULL`` propagates to ``NULL`` (not bucket 0), so a caller composing a
    ``COUNT(*) FILTER (WHERE bucket_col = i)`` histogram naturally excludes
    rows where *column* itself is unmeasured, rather than miscounting them as
    underflow. The delay histogram doesn't need that guard and is built in
    ClickHouse (``pipeline.ch_aggregates``), where ``dep_delay`` is filtered
    non-NULL upstream; a Postgres-built column that CAN be NULL per row (e.g.
    ``pipeline.dwell_run``'s dwell/running seconds) should use this helper
    instead of hand-rolling the same CASE shape.
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
