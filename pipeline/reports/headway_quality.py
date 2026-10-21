"""Excess Waiting Time / coefficient of variation / long-gap rate for
high-frequency routes, built on `agg_route_headway` /
`agg_route_headway_daily`.

Restricted to routes `agg_route_headway.is_high_frequency` classifies
high-frequency -- a route this repo hasn't classified as frequent has
nothing meaningful to compare an "excess" wait against, so it is omitted
from this report entirely, not returned with null metrics. This is a
second, narrower panel alongside the existing `on_time` report (see
`api/routers/reports.py`'s dedicated `/headway_quality` endpoint), not a
replacement for it -- a non-high-frequency route's `on_time` row is
completely unaffected.

See `pipeline.headways` for the shared reconstruction/formula module both
`pipeline.analyze`'s two headway builders and the query-time pooling below
depend on.
"""

from __future__ import annotations

from api.range import RangeCtx, dow_clause
from pipeline.day_types import agrees, load_day_types, load_dominant_day_types
from pipeline.headways import coefficient_of_variation_from_pooled, mean_wait_from_pooled
from pipeline.strategies.static_join import rt_field_coverage_confirmed


def _daily_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for `agg_route_headway_daily.date` -- date range + DOW
    + optional route filter.

    No service_type/time_band predicate: `agg_route_headway_daily` has
    neither dimension (a day's gaps are pooled across every service type
    and stop the route runs, see `pipeline.analyze`'s builder docstring), so
    a caller with a `time_band`/`service` filter set still gets an
    unfiltered-on-those-dimensions answer here -- there is nothing narrower
    to fall back to.

    `d.date` is a native DATE column (migration 0039), so -- like
    `pipeline.reports.filters._dist_filter` -- the cast lives on the
    parameter side to stay sargable, not on the column.
    """
    parts = [f"d.date >= (${next_param}::text)::date AND d.date <= (${next_param + 1}::text)::date"]
    params: list = [str(ctx.from_date), str(ctx.to_date)]
    n = next_param + 2

    frag, p, n = dow_clause("d.date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    if ctx.routes:
        parts.append(f"d.route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1

    return " AND ".join(parts), params, n


def _sum(values: list) -> float | None:
    """SQL SUM's NULL handling: the sum of the non-NULL values, NULL when there are none."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


async def compute_headway_quality(agency_id: int, ctx: RangeCtx, conn) -> list[dict]:
    """:func:`compute_headway_quality_report`'s rows."""
    rows, _ = await compute_headway_quality_report(agency_id, ctx, conn)
    return rows


async def compute_headway_quality_report(agency_id: int, ctx: RangeCtx, conn) -> tuple[list[dict], int]:
    """Per-route EWT / CoV / long-gap-rate, high-frequency routes only, and how
    many routes had data in range but none on a day of their class.

    Pools `agg_route_headway_daily`'s per-day sufficient statistics
    (`actual_headway_sum_sec` / `actual_headway_sumsq_sec2` /
    `long_gap_count`) over the in-range days pooled (below), summed as SQL SUM
    would sum them -- additive across any partition of the same gap population
    (see `pipeline.headways`'s module docstring), so this is bit-for-bit the
    same as computing the formulas directly over those days' gaps
    concatenated. EWT compares the pooled actual mean wait against
    `agg_route_headway.scheduled_wait_mean_sec` -- the SAME mean-wait
    formula applied to the static schedule's own headway distribution (see
    `pipeline.analyze`'s `agg_route_headway` builder) -- an apples-to-apples
    comparison, not actual-vs-median.

    The scheduled side is the route's dominant service alone (see
    `pipeline.headways.dominant_service_ctes`), so a day running another
    timetable -- a weekday route's weekends and holidays -- would read as late
    against it. Only the days whose day type (`pipeline.day_types`) agrees with
    that service's class are pooled, and `day_type` names the class. A route
    whose dominant service has no class is pooled over every day, with
    `day_type` None.

    A high-frequency route with zero `agg_route_headway_daily` rows in
    range (no RT-reconstructed headway data has accumulated yet), or none on
    a day of its class, is omitted entirely rather than returned with null
    metrics -- there is nothing yet to report for it.

    `agg_route_headway_daily` is materialized (see `pipeline.analyze`'s
    builder) for any `ingest_strategy` that CAN populate `stop_id`, which is
    a necessary but not sufficient condition -- an agency sharing that wire
    shape without being confirmed to actually populate `stop_id` on its own
    live feed would otherwise get its rows pooled here as if trustworthy.
    This additionally requires a live RT field-coverage verdict for the
    agency (via `pipeline.strategies.static_join.
    rt_field_coverage_confirmed`) before returning anything, returning no
    rows and a zero count for an unconfirmed agency rather than a
    possibly-empty-but-still-queried result.
    """
    if not await rt_field_coverage_confirmed(agency_id, conn):
        return [], 0

    frag, params, _ = _daily_filter(ctx, 2)
    sql = f"""
        SELECT h.route_code, h.scheduled_wait_mean_sec, d.date, d.actual_samples,
               d.actual_headway_sum_sec, d.actual_headway_sumsq_sec2, d.long_gap_count
        FROM agg_route_headway h
        JOIN agg_route_headway_daily d
          ON d.agency_id = h.agency_id AND d.route_code = h.route_code
        WHERE h.agency_id = $1 AND h.is_high_frequency
          AND {frag}
        ORDER BY h.route_code, d.date
    """
    rows = await conn.fetch(sql, agency_id, *params)
    classes = await load_dominant_day_types(conn, agency_id) if rows else {}
    day_types = (
        await load_day_types(conn, agency_id, ctx.from_date, ctx.to_date)
        if any(c is not None for c in classes.values())
        else None
    )

    by_route: dict[str, list] = {}
    for r in rows:
        cls = classes.get(r["route_code"])
        if cls is None or day_types is None or agrees(cls, day_types.of(r["date"])):
            by_route.setdefault(r["route_code"], []).append(r)

    out: list[dict] = []
    for route_code, days in by_route.items():
        n = sum(d["actual_samples"] for d in days)
        sum_sec = _sum([d["actual_headway_sum_sec"] for d in days])
        sumsq_sec2 = _sum([d["actual_headway_sumsq_sec2"] for d in days])
        actual_wait_mean_sec = mean_wait_from_pooled(n, sum_sec, sumsq_sec2)
        scheduled_wait_mean_sec = days[0]["scheduled_wait_mean_sec"]
        ewt_sec = (
            None
            if actual_wait_mean_sec is None or scheduled_wait_mean_sec is None
            else actual_wait_mean_sec - scheduled_wait_mean_sec
        )
        cov = coefficient_of_variation_from_pooled(n, sum_sec, sumsq_sec2)
        long_gap_count = _sum([d["long_gap_count"] for d in days])
        long_gap_rate = None if not n or long_gap_count is None else long_gap_count / n
        out.append(
            {
                "route_code": route_code,
                "ewt_sec": ewt_sec,
                "cov": cov,
                "long_gap_rate": long_gap_rate,
                "samples": n,
                "day_type": classes.get(route_code),
            }
        )
    off_timetable = len({r["route_code"] for r in rows} - by_route.keys())
    return out, off_timetable
