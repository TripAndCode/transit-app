"""Read side of the running time per timetable segment (`agg_route_segment_runtime`).

For one route over a request's range, each timetable segment (the stop pair the
timetable calls at in turn) and time band gets the observed running time's
median and 85th percentile, the scheduled running time of the same trip-days,
and the share of on-time-or-early arrivals at its first stop that were held
beyond the scheduled dwell. docs/methodology.md defines each figure.

Gated, like `pipeline.reports.dwell_run`, on the agency's live field-coverage
verdict: only a feed confirmed to send arrivals gives a running time.

A range spanning a timetable revision would average two timetables' scheduled
times into one neither has, so only the dates under the timetable in force on
the range's last day are read (`agg_schedule_revision_daily`), and the result
names that timetable.

Every figure is the feed's prediction (`value_basis`), not an observed
passage, until the agency's predictions are validated against observed
positions. No recommendation is derived from them here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from api.range import TIME_BAND_RANGES, RangeCtx
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.reports.definition import VALUE_BASIS
from pipeline.reports.filters import _dated_agg_filter
from pipeline.strategies.static_join import rt_field_coverage_gap

_BAND_ORDER = {band: i for i, band in enumerate(TIME_BAND_RANGES)}


def runtime_ctes(rows: str, *, key: Literal["time_band", "week_start"], version: str) -> str:
    """The CTEs ``ranged``, ``kept``, ``pooled`` and ``runs`` over *rows*, a
    SELECT of ``agg_route_segment_runtime`` rows carrying a *key* column.

    ``kept`` holds the rows dated under the timetable *version* names: a SQL
    expression evaluated per row (aliased ``s``), so one version can cover a
    whole range or each row's week its own. Pooling two timetables' scheduled
    times would state a timetable neither ran. ``pooled`` and ``runs`` group
    by stop pair and *key*: ``runs`` takes the exact, linearly interpolated
    median and 85th percentile of every pooled ``run_sec`` (Postgres
    ``percentile_cont``); ``pooled`` the scheduled sums, holds, the dates (``run_days``) and
    the routes with an observed run, and the lowest ``to_stop_sequence``.
    """
    return f"""ranged AS (
    SELECT s.*, r.static_version_id
    FROM ({rows}) s
    LEFT JOIN agg_schedule_revision_daily r ON r.agency_id = s.agency_id AND r.date = s.date
),
kept AS (SELECT * FROM ranged s WHERE s.static_version_id IS NOT DISTINCT FROM {version}),
pooled AS (
    SELECT from_stop_id, to_stop_id, {key}, MIN(to_stop_sequence) AS to_stop_sequence,
           COUNT(DISTINCT date) FILTER (WHERE cardinality(run_sec) > 0) AS run_days,
           array_agg(DISTINCT route_code) FILTER (WHERE cardinality(run_sec) > 0) AS routes,
           SUM(scheduled_samples) AS scheduled_samples, SUM(scheduled_sum_sec) AS scheduled_sum_sec,
           SUM(hold_eligible) AS hold_eligible, SUM(holds) AS holds
    FROM kept GROUP BY from_stop_id, to_stop_id, {key}
),
runs AS (
    SELECT from_stop_id, to_stop_id, {key}, COUNT(*) AS samples,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY x) AS p50,
           percentile_cont(0.85) WITHIN GROUP (ORDER BY x) AS p85
    FROM kept, unnest(run_sec) AS x
    GROUP BY from_stop_id, to_stop_id, {key}
)"""


def scheduled_run_sec(row) -> float | None:
    """A ``pooled`` row's mean scheduled running time, None without a sample."""
    return float(row["scheduled_sum_sec"] / row["scheduled_samples"]) if row["scheduled_samples"] else None


@perf.timed("reports.segment_runtime")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def compute_segment_runtime(agency_id: int, ctx: RangeCtx, conn, *, route: str) -> dict:
    """Per-segment running time for *route* over ``ctx``.

    Returns ``{"available", "unavailable_reason", "last_checked",
    "value_basis", "static_version_id", "days", "segments",
    "terminus_arrivals", "terminus_early_rate"}``.

    ``available`` False means the agency's arrival data is not trusted, with
    the reason (``pipeline.strategies.static_join.CoverageGap``); the caller
    states it rather than showing an empty table. ``static_version_id`` is the
    timetable the figures are read under (None where the ingest stamped none)
    and ``days`` how many of the range's dates were read.

    Each ``segments`` entry is ``{"from_stop_id", "from_stop_name",
    "to_stop_id", "to_stop_name", "time_band", "to_stop_sequence", "samples",
    "run_p50_sec", "run_p85_sec", "scheduled_run_sec", "padding_sec",
    "hold_eligible", "hold_rate"}``, ordered along the route by
    ``to_stop_sequence``, then by band. ``time_band`` is None for a segment the
    timetable gives no departure time for. The percentiles are exact, linearly
    interpolated (Postgres ``percentile_cont``); a segment with no observed
    running time in range has None there, and no ``padding_sec``.
    ``padding_sec`` is the scheduled running time minus the median: positive
    is slack, negative too tight. ``hold_rate`` is None when no arrival was
    eligible. ``terminus_early_rate`` is over every trip's arrivals at its last
    stop in range, None when there were none.
    """
    gap = await rt_field_coverage_gap(agency_id, conn)
    empty: dict = {
        "value_basis": VALUE_BASIS,
        "static_version_id": None,
        "days": 0,
        "segments": [],
        "terminus_arrivals": 0,
        "terminus_early_rate": None,
    }
    if gap is not None:
        return {"available": False, **gap.payload(), **empty}
    available = {"available": True, "unavailable_reason": None, "last_checked": None}

    # The route is named on its own, so a route filter the request carries does not narrow it away.
    # The latest revision on or before the range's last day, whether or not
    # the route ran under it; None for an agency whose ingest stamps no version.
    version = await conn.fetchval(
        "SELECT static_version_id FROM agg_schedule_revision_daily "
        "WHERE agency_id = $1 AND date <= $2 ORDER BY date DESC LIMIT 1",
        agency_id,
        ctx.to_date,
    )
    where, params, n = _dated_agg_filter(replace(ctx, routes=()), next_param=4)
    band_sql = ""
    if ctx.time_band in TIME_BAND_RANGES:
        band_sql = f" AND time_band = ${n}"
        params.append(ctx.time_band)
    rows = await conn.fetch(
        "WITH "
        + runtime_ctes(
            f"SELECT * FROM agg_route_segment_runtime WHERE agency_id = $1 AND route_code = $2 AND {where}{band_sql}",
            key="time_band",
            version="$3::text",
        )
        + "\nSELECT p.*, COALESCE(r.samples, 0) AS samples, r.p50, r.p85,\n"
        "       fs.stop_name AS from_stop_name, ts.stop_name AS to_stop_name,\n"
        "       (SELECT COUNT(DISTINCT date) FROM kept) AS days,\n"
        "       (SELECT SUM(terminal_arrivals) FROM kept) AS terminus_arrivals,\n"
        "       (SELECT SUM(terminal_early) FROM kept) AS terminus_early\n"
        "FROM pooled p\n"
        "LEFT JOIN runs r USING (from_stop_id, to_stop_id, time_band)\n"
        "LEFT JOIN static_stops fs ON fs.agency_id = $1 AND fs.stop_id = p.from_stop_id\n"
        "LEFT JOIN static_stops ts ON ts.agency_id = $1 AND ts.stop_id = p.to_stop_id",
        agency_id,
        str(route),
        version,
        *params,
    )
    if not rows:
        return {**available, **empty, "static_version_id": version}

    segments = []
    for r in rows:
        scheduled = scheduled_run_sec(r)
        p50 = float(r["p50"]) if r["p50"] is not None else None
        p85 = float(r["p85"]) if r["p85"] is not None else None
        segments.append(
            {
                "from_stop_id": r["from_stop_id"],
                "from_stop_name": r["from_stop_name"],
                "to_stop_id": r["to_stop_id"],
                "to_stop_name": r["to_stop_name"],
                "time_band": r["time_band"] if r["time_band"] in TIME_BAND_RANGES else None,
                "to_stop_sequence": r["to_stop_sequence"],
                "samples": r["samples"],
                "run_p50_sec": p50,
                "run_p85_sec": p85,
                "scheduled_run_sec": scheduled,
                "padding_sec": scheduled - p50 if scheduled is not None and p50 is not None else None,
                "hold_eligible": int(r["hold_eligible"]),
                "hold_rate": float(r["holds"] / r["hold_eligible"]) if r["hold_eligible"] else None,
            }
        )
    segments.sort(
        key=lambda s: (
            s["to_stop_sequence"],
            s["from_stop_id"],
            s["to_stop_id"],
            _BAND_ORDER.get(s["time_band"], len(_BAND_ORDER)),
        )
    )
    first = rows[0]
    terminus_arrivals = int(first["terminus_arrivals"] or 0)
    return {
        **available,
        "value_basis": VALUE_BASIS,
        "static_version_id": version,
        "days": first["days"],
        "segments": segments,
        "terminus_arrivals": terminus_arrivals,
        "terminus_early_rate": float(first["terminus_early"] / terminus_arrivals) if terminus_arrivals else None,
    }
