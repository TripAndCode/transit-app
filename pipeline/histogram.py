"""Fixed-width delay histogram: bucketing + percentile interpolation.

Powers the range-scoped report aggregates (``agg_route_daily_dist``). Exact
statistics (avg, on-time%, worst-5min) compose trivially across days by summing
counts; percentiles do **not**, so we store a per-day/route delay histogram and
interpolate p50/p90 from the merged buckets over the requested range. The
approximation error is bounded by ``WIDTH`` (one bucket), acceptable for ranking.

Bucketing here (analyze write path) and interpolation here (report read path)
share the same edges so the two never drift — change ``LO``/``HI``/``WIDTH`` in
one place and both sides move together.
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
