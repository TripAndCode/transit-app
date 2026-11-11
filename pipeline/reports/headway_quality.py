"""Excess Waiting Time / coefficient of variation / long-gap rate for
high-frequency routes, built on `agg_route_headway_daily`.

Each day is judged by the timetable in force on it: `agg_route_headway_daily`
carries, per (route, date), that timetable's scheduled median, high-frequency
test and scheduled sums (see `pipeline.analyze`'s builder and
`pipeline.static_headways`), and only the days a route is high-frequency on
are pooled. A route high-frequency on no day in range has nothing meaningful
to compare an "excess" wait against, so it is omitted from this report
entirely, not returned with null metrics. A day whose timetable keeps no
scheduled headways is not judged by another timetable in its place: it is
left out, and the answer counts it. This is a second, narrower panel
alongside the existing `on_time` report (see `api/routers/reports.py`'s
dedicated `/headway_quality` endpoint), not a replacement for it -- a
non-high-frequency route's `on_time` row is completely unaffected.

See `pipeline.headways` for the shared reconstruction/formula module both
`pipeline.analyze`'s two headway builders and the query-time pooling below
depend on.
"""

from __future__ import annotations

from typing import NamedTuple

from api.range import RangeCtx, dow_clause
from pipeline.day_types import agrees, load_day_types, load_dominant_day_types
from pipeline.headways import coefficient_of_variation_from_pooled, mean_wait_from_pooled, mean_wait_from_sums
from pipeline.strategies.static_join import rt_field_coverage_gap


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


def _sum_of_all(values: list) -> float | None:
    """The sum, or None when any value is: a total missing one day's part is no total."""
    return None if any(v is None for v in values) else sum(values)


# How many of the agency's routes the static timetable loaded now classified,
# and how many of those as high-frequency: what explains an empty answer when
# no day in range has a timetable to judge it by.
_FREQUENCY_SQL = """
    SELECT count(*) AS routes, count(*) FILTER (WHERE is_high_frequency) AS high_frequency
    FROM agg_route_headway
    WHERE agency_id = $1
"""

_AVAILABLE = {"unavailable_reason": None, "last_checked": None}
_NO_HIGH_FREQUENCY = {"unavailable_reason": "no_high_frequency_routes", "last_checked": None}


class HeadwayQualityReport(NamedTuple):
    """:func:`compute_headway_quality_report`'s answer."""

    rows: list[dict]
    # High-frequency routes with data in range, none of it on a day of their class.
    routes_off_timetable: int
    # Days in range holding a route-day whose timetable keeps no scheduled headways.
    days_timetable_not_kept: int
    # Days pooled whose timetable keeps its median but not its scheduled sums,
    # which leaves the excess wait of every route pooling one of them None.
    days_scheduled_wait_not_kept: int
    # {"unavailable_reason", "last_checked"}
    unavailable: dict


async def _no_high_frequency_route(agency_id: int, conn) -> bool:
    """Whether the timetable loaded now classified routes but none as high-frequency."""
    frequency = await conn.fetchrow(_FREQUENCY_SQL, agency_id)
    return bool(frequency["routes"]) and not frequency["high_frequency"]


async def compute_headway_quality(agency_id: int, ctx: RangeCtx, conn) -> list[dict]:
    """:func:`compute_headway_quality_report`'s rows."""
    return (await compute_headway_quality_report(agency_id, ctx, conn)).rows


async def compute_headway_quality_report(agency_id: int, ctx: RangeCtx, conn) -> HeadwayQualityReport:
    """Per-route EWT / CoV / long-gap-rate over the days each route is
    high-frequency on, how many routes had such days in range but none of
    their class, how many days in range were not judged, and why the panel is
    empty when it is bound to be.

    Each day is judged by the timetable in force on it (its row's scheduled
    columns, see the module docstring). A day whose timetable keeps no
    scheduled headways (`is_high_frequency` NULL) is pooled for no route and
    counted in `days_timetable_not_kept`, by its date.

    The unavailable value is ``{"unavailable_reason", "last_checked"}``. The
    reason is ``no_high_frequency_routes`` when no route is high-frequency,
    told only when no day in range lacks a kept timetable: by the days in
    range when one of them classified a route, else by the timetable loaded
    now classifying routes but none as high-frequency. Either way it comes
    from timetables alone, so no renewed feed check could fill the panel. It
    is told ahead of any coverage gap, then by the timetable loaded now, since
    an untrusted feed's days are not read. Otherwise it is the agency's
    ``pipeline.strategies.static_join.CoverageGap`` when the feed's fields
    are not trusted, and None when the rows are worth reading. The loaded
    timetable's classification is read only to explain an empty answer, so
    a response with rows never pays for it.

    Pools `agg_route_headway_daily`'s per-day sufficient statistics
    (`actual_headway_sum_sec` / `actual_headway_sumsq_sec2` /
    `long_gap_count`) over the in-range days pooled (below), summed as SQL SUM
    would sum them -- additive across any partition of the same gap population
    (see `pipeline.headways`'s module docstring), so this is bit-for-bit the
    same as computing the formulas directly over those days' gaps
    concatenated. EWT compares the pooled actual mean wait against the
    scheduled mean wait of the same days, pooled from each day's own
    timetable's sums (`scheduled_headway_sum_sec` /
    `scheduled_headway_sumsq_sec2`) -- the SAME mean-wait formula applied to
    the timetables' own headway distributions -- an apples-to-apples
    comparison, not actual-vs-median. A pooled day whose timetable was kept
    before its sums were leaves EWT None: its median judges it high-frequency
    and its gaps count, but the scheduled wait would be missing its part.
    `days_scheduled_wait_not_kept` counts such days, by their date.

    The scheduled side is the route's dominant service alone (see
    `pipeline.headways.dominant_service_ctes`), so a day running another
    service -- a weekday route's weekends and holidays -- would read as late
    against it. Only the days whose day type (`pipeline.day_types`) agrees with
    the loaded timetable's dominant service's class are pooled, and `day_type`
    names the class. A route whose dominant service has no class is pooled
    over every day, with `day_type` None.

    A route with no high-frequency `agg_route_headway_daily` row in range (no
    RT-reconstructed headway data has accumulated yet, or no day's timetable
    runs it often enough), or none on a day of its class, is omitted entirely
    rather than returned with null metrics -- there is nothing yet to report
    for it.

    `agg_route_headway_daily` is materialized (see `pipeline.analyze`'s
    builder) for any `ingest_strategy` that CAN populate `stop_id`, which is
    a necessary but not sufficient condition -- an agency sharing that wire
    shape without being confirmed to actually populate `stop_id` on its own
    live feed would otherwise get its rows pooled here as if trustworthy.
    This additionally requires a live RT field-coverage verdict for the
    agency (via `pipeline.strategies.static_join.
    rt_field_coverage_gap`) before returning anything, returning no
    rows and a zero count for an unconfirmed agency rather than a
    possibly-empty-but-still-queried result.
    """
    gap = await rt_field_coverage_gap(agency_id, conn)
    if gap is not None:
        if await _no_high_frequency_route(agency_id, conn):
            return HeadwayQualityReport([], 0, 0, 0, dict(_NO_HIGH_FREQUENCY))
        return HeadwayQualityReport([], 0, 0, 0, gap.payload())

    frag, params, _ = _daily_filter(ctx, 2)
    rows = await conn.fetch(
        f"""
        SELECT d.route_code, d.date, d.actual_samples, d.actual_headway_sum_sec, d.actual_headway_sumsq_sec2,
               d.long_gap_count, d.scheduled_headway_sum_sec, d.scheduled_headway_sumsq_sec2
        FROM agg_route_headway_daily d
        WHERE d.agency_id = $1 AND d.is_high_frequency AND {frag}
        ORDER BY d.route_code, d.date
        """,
        agency_id,
        *params,
    )
    judged = await conn.fetchrow(
        f"""
        SELECT count(DISTINCT d.date) FILTER (WHERE d.is_high_frequency IS NULL) AS days_not_kept,
               bool_or(d.scheduled_headway_median_sec IS NOT NULL) AS classified
        FROM agg_route_headway_daily d
        WHERE d.agency_id = $1 AND {frag}
        """,
        agency_id,
        *params,
    )
    days_not_kept = judged["days_not_kept"]
    if not rows and not days_not_kept:
        if judged["classified"] or await _no_high_frequency_route(agency_id, conn):
            return HeadwayQualityReport([], 0, 0, 0, dict(_NO_HIGH_FREQUENCY))
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
        scheduled_wait_mean_sec = mean_wait_from_sums(
            _sum_of_all([d["scheduled_headway_sum_sec"] for d in days]),
            _sum_of_all([d["scheduled_headway_sumsq_sec2"] for d in days]),
        )
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
    wait_not_kept = {
        d["date"]
        for days in by_route.values()
        for d in days
        if d["scheduled_headway_sum_sec"] is None or d["scheduled_headway_sumsq_sec2"] is None
    }
    return HeadwayQualityReport(out, off_timetable, days_not_kept, len(wait_not_kept), dict(_AVAILABLE))
