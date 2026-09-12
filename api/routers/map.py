"""Map-tab endpoints.

Three resources back the Map tab:

- ``GET /delays/live``: each current trip's latest reported stop and delay.
- ``GET /route-shape``: ordered stop sequence for one route plus, when
  the agency has loaded GTFS ``shapes.txt``, a real road-shape
  ``geometry`` field. Falls back to ``geometry: null`` so the frontend
  can draw a stop-coordinate polyline as a graceful degrade.
- ``GET /delays/heatmap``: per-stop average delay GeoJSON, scoped by
  the user's range / DOW / time-band filter. Stops are clustered by
  ``stop_name`` plus actual spatial proximity (``ST_ClusterDBSCAN``) so
  inbound/outbound platforms of the same logical stop merge into one circle.

The heatmap and route-shape endpoints honor :class:`~api.range.RangeCtx`
so the displayed colors match what compute_ranking et al. show under
the same filter.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from api.clickhouse import max_captured_at
from api.deps import get_agency, get_ch, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, build_agg_stop_filter, get_range_ctx
from api.triage import COHORT_LOW_CONFIDENCE_SAMPLES, LOW_CONFIDENCE_SAMPLES, classify_route
from pipeline.reports.map import compute_route_shape, route_exists

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/{agency_id}", tags=["map"])


def _as_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC tzinfo to a ClickHouse-returned naive datetime.

    ClickHouse's `updates.captured_at` is `DateTime64(0, 'UTC')`, but
    clickhouse-connect returns naive `datetime` objects for it (unlike
    asyncpg, which always returned a tz-aware value for the old
    `timestamptz` column). Every value that flows into a JSON response must
    be normalized here so `.isoformat()` keeps emitting the `+00:00`
    suffix — dropping it would silently change the wire format (and risks
    JS `new Date(...)` on the frontend misreading the string as local time).
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _round_half_up_int(x: float) -> int:
    """Round to the nearest int, half away from zero — matches Postgres's
    ``ROUND(x::numeric, 0)``, not Python's banker's-rounding ``round()``
    (see the repo's existing ``pipeline/reports/rankings.py::_avg_min`` for
    the same care applied to a different call site)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _latest_route_observation(conn, ch, agency_id: int, route_code: str) -> datetime | None:
    """Existence precheck + 30-day-bounded latest-observation probe.

    Shared by ``route_trips`` and ``route_stop_profile``, which both need
    "does this route exist, and if so what's its most recent observation
    within the last 30 days" before doing any further work. Returns None if
    the route has never been analyzed (see :func:`route_exists`) or has no
    ClickHouse observations within 30 days of the agency's own latest
    activity (anchored to the agency's own data, not wall-clock "now", so
    it's meaningful against replayed/old data too — a route with zero
    observations in that window still correctly resolves to None, even if
    it was active further in the past).
    """
    if not await route_exists(conn, agency_id, route_code):
        return None
    agency_latest = await max_captured_at(ch, agency_id)
    if agency_latest is None:
        return None
    route_probe_bound = agency_latest - timedelta(days=30)
    latest_result = await ch.query(
        "SELECT captured_at FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND route_code = {route:String} "
        "  AND captured_at >= {bound:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "route": route_code, "bound": route_probe_bound},
    )
    return _as_utc(latest_result.result_rows[0][0] if latest_result.result_rows else None)


@router.get("/delays/live")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def live_delays(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    limit: int = Query(default=500, le=500),
):
    """Latest reported stop and delay for trips in the current feed window."""
    latest_ts = await max_captured_at(ch, agency_id)
    if latest_ts is None:
        return {"latest_captured_at": None, "rows": []}

    # A poll can report several future stops for one trip. The lowest sequence
    # in the newest poll is the nearest reported stop and wins the final tie.
    rows_result = await ch.query(
        """
        SELECT trip_id, winner.1 AS route_code, winner.2 AS service_type,
            winner.3 AS scheduled_time, winner.4 AS dep_delay,
            winner.5 AS stop_id, winner.6 AS stop_sequence, captured_at
        FROM (
            SELECT u.trip_id AS trip_id,
                argMax(
                    tuple(
                        u.route_code, u.service_type, u.scheduled_time,
                        u.dep_delay, u.stop_id, u.stop_sequence
                    ),
                    (u.captured_at, u.file_name, -toInt32(u.stop_sequence))
                ) AS winner,
                max(u.captured_at) AS captured_at
            FROM updates AS u
            WHERE u.agency_id = {agency_id:UInt16}
              AND u.dep_delay IS NOT NULL
              AND u.captured_at >= {latest_ts:DateTime64} - INTERVAL 5 MINUTE
            GROUP BY u.trip_id
        ) AS grouped
        ORDER BY trip_id
        LIMIT {limit:UInt32}
        """,
        parameters={"agency_id": agency_id, "latest_ts": latest_ts, "limit": limit},
    )
    out_rows = []
    for r in rows_result.result_rows:
        row = dict(zip(rows_result.column_names, r, strict=True))
        row["captured_at"] = _as_utc(row["captured_at"])
        # scheduled_time's wire format used to be uniform (Postgres TIME ->
        # "HH:MM:SS" for every agency); now it's whatever the ingest strategy
        # stored ("HH:MM" for aomori_regex, "HH:MM:SS" for static_join).
        # Restore the original "HH:MM:SS" contract for every agency.
        if row["scheduled_time"] is not None and len(row["scheduled_time"]) < 8:
            row["scheduled_time"] = f"{row['scheduled_time']}:00"
        out_rows.append(row)

    metadata_by_trip: dict[str, dict] = {}
    if out_rows:
        trip_ids = [row["trip_id"] for row in out_rows]
        stop_sequences = [row["stop_sequence"] for row in out_rows]
        rt_stop_ids = [row["stop_id"] for row in out_rows]
        # Stop/headsign enrichment is non-critical relative to the ClickHouse
        # trip/delay data above (same "one sub-check must not sink the whole
        # response" shape as the freshness probe elsewhere in this file) — a
        # Postgres hiccup degrades to missing stop metadata, not a 500.
        try:
            metadata_rows = await conn.fetch(
                """
                WITH live AS (
                    SELECT *
                    FROM unnest($2::text[], $3::integer[], $4::text[])
                        AS x(trip_id, stop_sequence, rt_stop_id)
                )
                SELECT live.trip_id,
                       COALESCE(rt_stop.stop_id, scheduled_stop.stop_id) AS stop_id,
                       COALESCE(rt_stop.stop_name, scheduled_stop.stop_name) AS stop_name,
                       COALESCE(rt_stop.stop_lat, scheduled_stop.stop_lat) AS stop_lat,
                       COALESCE(rt_stop.stop_lon, scheduled_stop.stop_lon) AS stop_lon,
                       st.trip_headsign
                FROM live
                LEFT JOIN static_stop_times sst
                  ON sst.agency_id = $1
                 AND sst.trip_id = live.trip_id
                 AND sst.stop_sequence = live.stop_sequence
                LEFT JOIN static_stops rt_stop
                  ON rt_stop.agency_id = $1
                 AND rt_stop.stop_id = NULLIF(live.rt_stop_id, '')
                LEFT JOIN static_stops scheduled_stop
                  ON scheduled_stop.agency_id = $1
                 AND scheduled_stop.stop_id = sst.stop_id
                LEFT JOIN static_trips st
                  ON st.agency_id = $1 AND st.trip_id = live.trip_id
                """,
                agency_id,
                trip_ids,
                stop_sequences,
                rt_stop_ids,
            )
            metadata_by_trip = {row["trip_id"]: dict(row) for row in metadata_rows}
        except Exception:
            _log.warning(
                "Postgres stop-metadata enrichment failed for agency %s — degrading to missing stop info",
                agency_id,
                exc_info=True,
            )

    for row in out_rows:
        metadata = metadata_by_trip.get(row["trip_id"], {})
        row["stop_id"] = metadata.get("stop_id") or row["stop_id"]
        row["stop_name"] = metadata.get("stop_name")
        row["stop_lat"] = metadata.get("stop_lat")
        row["stop_lon"] = metadata.get("stop_lon")
        row["headsign"] = metadata.get("trip_headsign")
    return {
        "latest_captured_at": latest_ts.isoformat(),
        "rows": out_rows,
    }


@router.get("/route-shape")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def route_shape(
    request: Request,
    route: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
):
    """Ordered stop sequence + per-stop avg delay for one route over ctx.

    Returns ``{ route, geometry, stops: [{ stop_sequence, stop_name, stop_id,
    stop_code, platform_code, lon, lat, avg_min, samples }], unobserved_stops }``.
    Powers the Map tab's per-route overlay (polyline + numbered stops) when the
    user filters to a single route.

    The optional GTFS identifiers (``stop_id`` / ``stop_code`` /
    ``platform_code``) are included so the unified popup template renders
    the same fields it shows for the heatmap layer — without them route
    mode would silently drop the pole badge and stop_id footer.
    """
    return await compute_route_shape(conn, ch, agency_id, str(route), ctx)


@router.get("/today/route-summary")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def today_route_summary(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
):
    """Per-route triage summary for the most recent analyzed date.

    Powers the 最新観測 tab. Each row carries the latest analyzed day's figures
    (``avg_delay_sec``, ``worst_delay_sec``, ``trips_observed``, ``samples``,
    ``last_seen_at``, ``service_type``) joined to the historical baseline in
    ``agg_route_stats`` (``baseline_avg_sec``, ``baseline_p90_sec``). A pure
    classifier (:func:`api.triage.classify_route`) then assigns each route a
    ``bucket`` (anomaly / watch / normal / no_baseline), a ``deviation_sec``
    (today vs baseline), and a ``low_confidence`` flag for thin TODAY samples.
    ``baseline_samples`` is the separate, un-gated sample count backing the
    baseline itself (``agg_route_stats`` no longer drops thin route/service
    groups at insert time) — the client decides its own low-confidence
    treatment for a thin baseline from this field. The client groups by
    bucket, so the SQL ``ORDER BY`` is only a sensible default.

    Reads the precomputed ``agg_route_daily`` (built by ``analyze``) for the
    latest date instead of scanning raw ``updates`` — a small indexed read
    regardless of agency size; "today" therefore means "as of the last analyze".
    """
    latest_date = await conn.fetchval(
        "SELECT MAX(date) FROM agg_route_daily WHERE agency_id=$1",
        agency_id,
    )
    if latest_date is None:
        # Agency ingested but not yet analyzed (or brand-new): no agg rows yet.
        # Return empty rather than falling back to a raw `updates` scan — the
        # window is one cron cycle (ingest+analyze run together), and the live
        # scan is exactly the cost this endpoint exists to avoid.
        return {"latest_captured_at": None, "date": None, "routes": [], "raw_samples": 0, "clamp_count": 0}

    rows = await conn.fetch(
        """
        WITH rb AS (
            -- Route-grain baseline (across service_types), so a NULL-service daily
            -- row (stored as '') still finds a baseline even though agg_route_stats
            -- has no '' row. Mirrors the digest's route-grain baseline
            -- (pipeline/digest/build.py's _ROUTE_BASELINE_SQL) for both columns.
            -- base_avg_min is FILTERed the same way as base_p90_min below:
            -- sum_delay_sec is nullable (unlike samples, unlike AVG()-backed
            -- avg_min), so a pre-backfill NULL row's samples must not count in
            -- the denominator without also contributing to the numerator, or
            -- base_avg_min would be biased toward zero whenever any
            -- contributing service_type hasn't been backfilled yet.
            -- base_p90_min's numerator/denominator are both FILTERed to the same
            -- p90_min IS NOT NULL rows: `analyze()`'s own SQL can no longer
            -- produce a null p90_min alongside a non-null avg_min/samples for a
            -- live group (dep_delay is filtered non-null upstream, and analyze()
            -- wipes and rebuilds each agency's rows from scratch every run), but
            -- this FILTER stays as defense-in-depth against a stale pre-rebuild
            -- row or a non-analyze() writer (e.g. a test fixture) inserting one
            -- directly -- SUM() silently
            -- skips a null numerator term but NOT its row's sample count in the
            -- denominator, which would otherwise bias base_p90_min down whenever
            -- any contributing service_type's row is null this way.
            -- base_p90_min itself is a samples-weighted average of each
            -- service_type's already-computed p90_min, not a percentile
            -- recomputed over the pooled raw delay observations across
            -- service_types -- agg_route_stats stores only a per-group p90
            -- and sample count, never the raw distribution, so an exact
            -- pooled percentile isn't computable from it. Same defensible-
            -- approximation shape as the heatmap's p90_delay_min elsewhere
            -- in this file.
            SELECT route_code,
                   SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric
                       / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0 AS base_avg_min,
                   SUM(p90_min * samples) FILTER (WHERE p90_min IS NOT NULL)
                       / NULLIF(SUM(samples) FILTER (WHERE p90_min IS NOT NULL), 0) AS base_p90_min,
                   SUM(samples) AS base_samples
            FROM agg_route_stats
            WHERE agency_id = $1 AND samples IS NOT NULL
            GROUP BY route_code
        )
        SELECT
            d.route_code, d.service_type, d.avg_delay_sec, d.worst_delay_sec,
            d.trips_observed, d.samples, d.last_seen_at,
            -- All three baseline columns are picked from the SAME source
            -- (b or rb) via one shared condition, never coalesced
            -- independently per column -- b.avg_min IS NOT NULL is the
            -- correct "does b have a matching row" test (AVG() over a real
            -- joined row is never null). `analyze()`'s own SQL can no longer
            -- produce a null b.p90_min alongside a non-null b.avg_min for a
            -- live (route, service_type) group (dep_delay is filtered
            -- non-null upstream, and analyze() wipes and rebuilds every row
            -- each run), but a stale pre-rebuild row or a non-analyze()
            -- writer could still leave one, so this guard stays; independently
            -- coalescing each column would then silently mix b's exact-match
            -- avg with rb's pooled-across-service_types p90 -- two different
            -- statistical populations reported as one baseline. Picking all
            -- three from the same side means baseline_p90_min can be null
            -- even when baseline_avg_min isn't (classify_route already
            -- treats any null baseline input as "no_baseline"), which is
            -- correct: a missing same-source p90 must not be papered over
            -- with a different population's figure.
            CASE WHEN b.avg_min IS NOT NULL THEN b.avg_min ELSE rb.base_avg_min END AS baseline_avg_min,
            CASE WHEN b.avg_min IS NOT NULL THEN b.p90_min ELSE rb.base_p90_min END AS baseline_p90_min,
            -- baseline_samples backs whichever source above was actually used,
            -- so the client can flag a thin baseline -- not folded into
            -- classify_route/low_confidence, which judges TODAY's sample count.
            CASE WHEN b.avg_min IS NOT NULL THEN b.samples ELSE rb.base_samples END AS baseline_samples,
            b.late5_pct
        FROM agg_route_daily d
        LEFT JOIN agg_route_stats b
          ON b.agency_id = $1
         AND b.route_code = d.route_code
         AND b.service_type = d.service_type
        LEFT JOIN rb ON rb.route_code = d.route_code
        WHERE d.agency_id = $1 AND d.date = $2
        ORDER BY d.worst_delay_sec DESC, d.route_code
        """,
        agency_id,
        latest_date,
    )

    # Freshness header reflects INGEST recency (what DataStalenessBanner means),
    # not analyze recency — a cheap probe, independent of the agg. ORDER BY
    # captured_at DESC LIMIT 1 (not maxOrNull) is served off the sort index
    # instead of a full per-agency scan — see live_delays above / the
    # pipeline/clickhouse.py::max_captured_at docstring.
    #
    # Purely informational: every substantive row below comes from Postgres
    # agg_* tables, so a ClickHouse hiccup on this one freshness lookup must
    # not 500 the whole endpoint — degrade to latest_captured_at=None instead
    # (same "one non-critical sub-check shouldn't sink an otherwise-fine
    # response" shape as pipeline.health.aggregate_freshness's degrade on
    # agg_feed_health / api.routers.admin.admin_ops's per-sub-check try/except).
    latest_ts = None
    try:
        latest_result = await ch.query(
            "SELECT captured_at FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY captured_at DESC LIMIT 1",
            parameters={"agency_id": agency_id},
        )
        latest_ts = _as_utc(latest_result.result_rows[0][0] if latest_result.result_rows else None)
    except Exception:
        _log.warning("ClickHouse freshness probe failed for agency %s — degrading to null", agency_id, exc_info=True)

    # Feed-health over the last 7 analyzed days (not just the latest): frozen/stale
    # feeds recur across days, so a single clean latest day must not hide a feed
    # that froze earlier in the window. Powers FeedHealthBanner; small indexed read,
    # defaults to 0 when no rows (pre-migration / not re-analyzed).
    fh = await conn.fetchrow(
        "SELECT COALESCE(SUM(raw_samples), 0) AS raw_samples, "
        "       COALESCE(SUM(clamp_count), 0) AS clamp_count "
        "FROM agg_feed_health WHERE agency_id=$1 AND date >= $2::date - 6",
        agency_id,
        latest_date,
    )

    routes = []
    for r in rows:
        baseline_avg_sec = round(r["baseline_avg_min"] * 60) if r["baseline_avg_min"] is not None else None
        baseline_p90_sec = round(r["baseline_p90_min"] * 60) if r["baseline_p90_min"] is not None else None
        bucket, deviation_sec, low_confidence = classify_route(
            r["avg_delay_sec"], baseline_avg_sec, baseline_p90_sec, r["samples"]
        )
        routes.append(
            {
                "route_code": r["route_code"],
                # '' is the NULL-service sentinel from agg_route_daily — map back.
                "service_type": r["service_type"] or None,
                "avg_delay_sec": r["avg_delay_sec"],
                "worst_delay_sec": r["worst_delay_sec"],
                "trips_observed": r["trips_observed"],
                "samples": r["samples"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "baseline_avg_sec": baseline_avg_sec,
                "baseline_p90_sec": baseline_p90_sec,
                "baseline_samples": r["baseline_samples"],
                "deviation_sec": deviation_sec,
                "bucket": bucket,
                "low_confidence": low_confidence,
                # bucket=="no_baseline" whenever classify_route treats any of
                # avg/p90 as missing -- has_baseline must track that exactly
                # (not just baseline_avg_sec) so a thin group with a real avg
                # but a null p90 doesn't render as both "no baseline yet" and
                # a concrete today-vs-baseline comparison at once.
                "has_baseline": bucket != "no_baseline",
                "late5_pct": r["late5_pct"],
            }
        )
    return {
        "latest_captured_at": latest_ts.isoformat() if latest_ts else None,
        "date": latest_date.isoformat(),
        "routes": routes,
        "raw_samples": fh["raw_samples"] if fh else 0,
        "clamp_count": fh["clamp_count"] if fh else 0,
    }


@router.get("/today/route/{route_code}/trips")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def route_trips(
    request: Request,
    route_code: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
):
    """Per-trip delay for one route on the latest observation date.

    One row per trip_id: representative scheduled departure (HH:MM), headsign
    (from static_trips), and the trip's average dep_delay across its stops.
    Sorted worst-first — answers "which buses were late". Read-only.
    """
    # Cheap existence precheck + 30-day-bounded latest-observation probe FIRST,
    # before any further ClickHouse work: a fabricated/nonexistent route_code
    # on this anonymous, reachable endpoint must cost ~0 ClickHouse work, not
    # just a bounded-but-still-huge scan (a date bound alone isn't enough
    # while every agency's full history still fits inside the bound's
    # window). See `_latest_route_observation` for the full existence-check
    # and bound rationale.
    latest_ts = await _latest_route_observation(conn, ch, agency_id, route_code)
    if latest_ts is None:
        return {"date": None, "trips": []}

    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring).
    # Two non-key columns (scheduled_time, dep_delay) are read off the SAME
    # winning row, so they're packed into ONE tuple-argMax rather than one
    # argMax per column — per-column argMax on a captured_at tie could
    # silently mix columns from two different physical rows. Unpacked by
    # position in the outer SELECT to keep the result's column order exactly
    # `trip_id, stop_sequence, scheduled_time, dep_delay` (this function
    # unpacks each row by position below). `ORDER BY trip_id` on the outer
    # select restores the deterministic row order the old sort-based form got
    # for free from its own ORDER BY — a bare GROUP BY has no defined output
    # order, and this route's row count (~1.7k) makes the sort cheap.
    dedup_result = await ch.query(
        """
        SELECT trip_id, stop_sequence, winner.1 AS scheduled_time, winner.2 AS dep_delay
        FROM (
            SELECT u.trip_id AS trip_id, u.stop_sequence AS stop_sequence,
                argMax(tuple(u.scheduled_time, u.dep_delay), (u.captured_at, u.file_name)) AS winner
            FROM updates AS u
            WHERE u.agency_id = {agency_id:UInt16} AND u.route_code = {route:String}
              AND u.dep_delay IS NOT NULL
              AND toDate(u.captured_at, 'Asia/Tokyo') = toDate({latest_ts:DateTime64}, 'Asia/Tokyo')
            GROUP BY u.trip_id, u.stop_sequence
        ) AS grouped
        ORDER BY trip_id
        """,
        parameters={"agency_id": agency_id, "route": route_code, "latest_ts": latest_ts},
    )
    per_trip: dict[str, dict] = defaultdict(lambda: {"scheduled_times": [], "delays": []})
    for trip_id, _stop_sequence, scheduled_time, dep_delay in dedup_result.result_rows:
        t = per_trip[trip_id]
        if scheduled_time is not None:
            t["scheduled_times"].append(scheduled_time)
        t["delays"].append(dep_delay)

    trip_ids = list(per_trip.keys())
    headsigns: dict[str, str | None] = {}
    if trip_ids:
        headsign_rows = await conn.fetch(
            "SELECT trip_id, trip_headsign FROM static_trips WHERE agency_id = $1 AND trip_id = ANY($2)",
            agency_id,
            trip_ids,
        )
        for r in headsign_rows:
            headsigns[r["trip_id"]] = r["trip_headsign"]

    trips: list[dict[str, Any]] = []
    for trip_id, t in per_trip.items():
        delays = t["delays"]
        avg_delay_sec = _round_half_up_int(sum(delays) / len(delays)) if delays else None
        sched = min(t["scheduled_times"]) if t["scheduled_times"] else None
        trips.append(
            {
                "trip_id": trip_id,
                "scheduled_time": sched[:5] if sched else None,
                "headsign": headsigns.get(trip_id),
                "avg_delay_sec": avg_delay_sec,
                "samples": len(delays),
            }
        )
    trips.sort(key=lambda t: (t["avg_delay_sec"] is None, -(t["avg_delay_sec"] or 0)))
    return {
        "date": latest_ts.date().isoformat(),
        "trips": trips,
    }


def _cohort_fields(stop_id: str | None, route_avg_sec: int, cohort: dict) -> dict:
    """Merge cohort stats for one stop into the stop dict.

    ``cohort_low_confidence`` flags a thin total observation count behind
    ``cohort_avg_delay_sec`` — independent of ``is_outlier``'s own
    ``cohort_route_count >= 2`` gate, which only checks how many DISTINCT
    routes contributed, not how many total observations they contributed
    between them (two routes with 5 observations each still clears that
    gate but is still a thin average).
    """
    if stop_id is None or stop_id not in cohort:
        return {
            "cohort_avg_delay_sec": None,
            "cohort_route_count": 0,
            "cohort_samples": 0,
            "cohort_low_confidence": False,
            "is_outlier": False,
        }
    c = cohort[stop_id]
    cohort_avg = c["cohort_avg_delay_sec"]
    route_count = c["cohort_route_count"]
    cohort_samples = c["cohort_samples"] or 0
    is_outlier = cohort_avg is not None and route_count >= 2 and route_avg_sec > cohort_avg * 1.5
    return {
        "cohort_avg_delay_sec": cohort_avg,
        "cohort_route_count": route_count,
        "cohort_samples": cohort_samples,
        "cohort_low_confidence": cohort_avg is not None and cohort_samples < COHORT_LOW_CONFIDENCE_SAMPLES,
        "is_outlier": is_outlier,
    }


@router.get("/today/route/{route_code}/stop-profile")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def route_stop_profile(
    request: Request,
    route_code: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
):
    """Average delay per stop_sequence along one route on the latest date.

    Joins observed (trip_id, stop_sequence) to static_stops for a stop name,
    ordered by sequence — answers "where on the route does delay build". The
    name is best-effort (MAX over the sequence's mapped stop). Read-only.
    """
    # Existence precheck + bounded route-scoped probe — see
    # `_latest_route_observation` for the full rationale (same
    # fabricated-route-code / unbounded-scan vulnerability, same fix, shared
    # with route_trips above).
    latest_ts = await _latest_route_observation(conn, ch, agency_id, route_code)
    if latest_ts is None:
        return {"date": None, "stops": []}

    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring) —
    # only one non-key column (dep_delay) is read off the winning row, so a
    # single argMax suffices.
    dedup_result = await ch.query(
        """
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay
        FROM updates AS u
        WHERE u.agency_id = {agency_id:UInt16} AND u.route_code = {route:String}
          AND u.dep_delay IS NOT NULL
          AND toDate(u.captured_at, 'Asia/Tokyo') = toDate({latest_ts:DateTime64}, 'Asia/Tokyo')
        GROUP BY u.trip_id, u.stop_sequence
        """,
        parameters={"agency_id": agency_id, "route": route_code, "latest_ts": latest_ts},
    )
    dedup_rows = list(dedup_result.result_rows)

    static_join_rows: list = []
    if dedup_rows:
        dedup_trip_ids = list({tid for tid, _, _ in dedup_rows})
        static_join_rows = await conn.fetch(
            "SELECT sst.trip_id, sst.stop_sequence, sst.stop_id, ss.stop_name "
            "FROM static_stop_times sst "
            "LEFT JOIN static_stops ss ON ss.agency_id = $1 AND ss.stop_id = sst.stop_id "
            "WHERE sst.agency_id = $1 AND sst.trip_id = ANY($2)",
            agency_id,
            dedup_trip_ids,
        )
    static_by_pair = {(r["trip_id"], r["stop_sequence"]): r for r in static_join_rows}

    per_seq: dict[int, dict] = defaultdict(lambda: {"delays": [], "stop_ids": [], "stop_names": []})
    for trip_id, stop_sequence, dep_delay in dedup_rows:
        a = per_seq[stop_sequence]
        a["delays"].append(dep_delay)
        info = static_by_pair.get((trip_id, stop_sequence))
        if info is not None:
            if info["stop_id"] is not None:
                a["stop_ids"].append(info["stop_id"])
            if info["stop_name"] is not None:
                a["stop_names"].append(info["stop_name"])

    rows: list[dict[str, Any]] = [
        {
            "stop_sequence": seq,
            "stop_id": max(a["stop_ids"]) if a["stop_ids"] else None,
            "stop_name": max(a["stop_names"]) if a["stop_names"] else None,
            "avg_delay_sec": _round_half_up_int(sum(a["delays"]) / len(a["delays"])) if a["delays"] else None,
            "samples": len(a["delays"]),
        }
        for seq, a in sorted(per_seq.items())
    ]

    # Build cohort stats per stop_id from agg_route_stop_daily (last 30 days).
    stop_ids = [r["stop_id"] for r in rows if r["stop_id"] is not None]
    cohort_by_stop: dict[str, dict] = {}
    if stop_ids:
        date_from = latest_ts.date() - timedelta(days=30)
        cohort_rows = await conn.fetch(
            """
            SELECT
                stop_id,
                COUNT(DISTINCT route_code) AS cohort_route_count,
                SUM(samples)::int AS cohort_samples,
                ROUND(
                    (SUM(delay_sum)::float / NULLIF(SUM(samples), 0))::numeric, 0
                )::int AS cohort_avg_delay_sec
            FROM agg_route_stop_daily
            WHERE agency_id = $1
              AND stop_id = ANY($2)
              AND date >= $3
            GROUP BY stop_id
            """,
            agency_id,
            stop_ids,
            date_from,
        )
        cohort_by_stop = {cr["stop_id"]: dict(cr) for cr in cohort_rows}

    return {
        "date": latest_ts.date().isoformat(),
        "stops": [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_id": r["stop_id"],
                "stop_name": r["stop_name"],
                "avg_delay_sec": r["avg_delay_sec"],
                "samples": r["samples"],
                **_cohort_fields(r["stop_id"], r["avg_delay_sec"], cohort_by_stop),
            }
            for r in rows
        ],
    }


def _heatmap_features(rows) -> dict:
    """Build a GeoJSON FeatureCollection from query rows.

    Each row must have columns: lon, lat, stop_name, stop_ids, platform_codes,
    stop_codes, route_codes, avg_delay_min, p90_delay_min, samples.  Those columns
    are mapped to the GeoJSON Feature properties (stop_id, stop_name, stop_code,
    platform_code, avg_delay_min, p90_delay_min, samples, route_codes,
    low_confidence).

    ``low_confidence`` reuses the same sample-count floor as the route-level
    baselines (``LOW_CONFIDENCE_SAMPLES``) rather than the cohort-specific one:
    a heatmap cell pools the FULL requested date range (not a fixed 30-day
    window at one stop), so its typical sample density is closer to a route
    baseline's than to a cohort comparison's.
    """
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(r["lon"]), 6), round(float(r["lat"]), 6)]},
            "properties": {
                "stop_id": r["stop_ids"],
                "stop_name": r["stop_name"],
                "stop_code": r["stop_codes"] or "",
                "platform_code": r["platform_codes"] or "",
                "avg_delay_min": float(r["avg_delay_min"]),
                "p90_delay_min": float(r["p90_delay_min"]) if r["p90_delay_min"] is not None else None,
                "samples": r["samples"],
                "route_codes": r["route_codes"] or "",
                "low_confidence": r["samples"] < LOW_CONFIDENCE_SAMPLES,
            },
        }
        for r in rows
        if r["lon"] is not None and r["lat"] is not None
    ]
    return {"type": "FeatureCollection", "features": features}


@router.get("/delays/heatmap")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def delay_heatmap(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
):
    """Per-stop average delay GeoJSON, scoped to the request's range/DOW/time-band.

    Clustering: two physical platforms with the same ``stop_name`` within
    ~550 m (``ST_ClusterDBSCAN(geom, eps := 0.005, minpoints := 1)``,
    partitioned by name so only same-named stops can merge) collapse into one
    circle. ``minpoints := 1`` means every point is a core point, so DBSCAN
    chains transitively (A-B-C merge if each consecutive hop is within
    ``eps``, even if A-C alone exceeds it) — real multi-platform hubs are
    exactly this shape (checked on real data: the widest legitimate hubs
    chain up to ~580m total span, but no single hop between platforms of
    the same hub exceeds ~320m, and the next-nearest *coincidental* reuse of
    a name starts at ~19 km away). ``eps`` sits well above the real hop
    ceiling and nowhere near that 19 km gap, so it merges every genuine hub
    without bridging unrelated same-named stops — an oversized `eps` (the
    first version of this fix reused the old grid's ~5 km CELL SIZE as if it
    were a merge RADIUS, a different quantity) chained across multiple
    unrelated stops on real data. DBSCAN clusters by actual pairwise distance
    rather than a fixed grid, so two close platforms also can't fail to merge
    purely from straddling a grid-cell boundary the way ``ST_SnapToGrid`` did
    (confirmed on real data, ~1.2% of same-named pairs within 200m). Stops
    without a ``stop_name`` fall back to a synthetic ``stop_id``-based key,
    so each stands alone (its own singleton partition — DBSCAN never runs on
    more than one point per partition there).

    Output coordinates are the centroid of the merged poles so the dot sits
    between paired platforms rather than on one of them.

    Served entirely from precomputed aggregates (no live ``updates`` scan): the
    no-route case reads ``agg_stop_daily``; a route filter reads
    ``agg_route_stop_daily`` (pre-split by ``route_code``). Both aggregates are
    deduped to one row per trip-stop event, so ``samples`` is an observation count.
    """
    # `name_key` names the partition each cluster is confined to: same key ->
    # DBSCAN may merge; different key -> never (guarantees name is never lost
    # across a merge, and unnamed stops — key is already unique per stop_id —
    # each land alone). `cluster_id` is DBSCAN's within-partition cluster label.
    # Computed once over `static_stops` (a few thousand rows/agency) rather
    # than inline against the agg join — running the window function per
    # *stop* instead of per (stop, date, time_band) agg row it joins to is
    # cheaper by construction, since the stop set is far smaller than the
    # agg join it would otherwise run against.
    # `name_key` is computed in an inner SELECT so PARTITION BY can reference
    # its alias once, rather than repeating the CASE expression.
    stop_clusters_cte = """
        stop_clusters AS (
            SELECT stop_id, stop_name, platform_code, stop_code, geom, name_key,
                ST_ClusterDBSCAN(geom, eps := 0.005, minpoints := 1) OVER (PARTITION BY name_key) AS cluster_id
            FROM (
                SELECT stop_id, stop_name, platform_code, stop_code, geom,
                    CASE WHEN NULLIF(stop_name, '') IS NOT NULL THEN stop_name ELSE 'unnamed:' || stop_id END
                        AS name_key
                FROM static_stops
                WHERE agency_id = $1 AND geom IS NOT NULL
            ) named
        )
    """
    # Shared by both branches below: aggregates `joined` rows into one heatmap
    # feature per (name_key, cluster_id). `joined` aliases each branch's route
    # column to the common name `route_code_val` (a.route_code vs. r.route_codes)
    # so this one projection works for both — the only thing that actually
    # differs between the branches is how `joined` is built (which agg
    # table/filter feeds it).
    cluster_projection_sql = """
        SELECT
            AVG(ST_X(geom))::numeric AS lon,
            AVG(ST_Y(geom))::numeric AS lat,
            string_agg(DISTINCT stop_name, ' / ' ORDER BY stop_name) AS stop_name,
            string_agg(DISTINCT stop_id, ',') AS stop_ids,
            string_agg(DISTINCT NULLIF(platform_code, ''), ',' ORDER BY NULLIF(platform_code, ''))
                AS platform_codes,
            string_agg(DISTINCT NULLIF(stop_code, ''), ' / ' ORDER BY NULLIF(stop_code, ''))
                AS stop_codes,
            string_agg(DISTINCT route_code_val, ',' ORDER BY route_code_val) AS route_codes,
            ROUND(SUM(delay_sum)::numeric / SUM(samples) / 60.0, 2) AS avg_delay_min,
            -- p90_delay_min runs PERCENTILE_CONT(0.9) over the joined rows'
            -- own per-row averages (delay_sum/samples for each pre-cluster
            -- stop/date/time_band row), not over the underlying raw
            -- per-observation delays -- the agg schema stores only a summed
            -- delay and a sample count per row, never the raw distribution,
            -- so an exact percentile of individual observations isn't
            -- computable from it. This is a percentile of row-level
            -- averages: a defensible approximation given the schema, but a
            -- different statistic from a true p90 of raw delays.
            ROUND(
                PERCENTILE_CONT(0.9) WITHIN GROUP (
                    ORDER BY delay_sum::float / NULLIF(samples, 0)
                )::numeric / 60.0,
            2) AS p90_delay_min,
            SUM(samples) AS samples
        FROM joined
        GROUP BY name_key, cluster_id
    """
    if ctx.routes:
        # Route filter → aggregate path (agg_route_stop_daily is pre-split by route_code).
        # Mirrors the no-route branch's spatial grouping; adds a route_code = ANY($2)
        # filter. $1=agency_id, $2=route list, so the ctx filter starts at $3.
        agg_where, params, _ = build_agg_stop_filter(ctx, next_param=3)
        rows = await conn.fetch(
            f"""
            WITH {stop_clusters_cte},
            joined AS (
                SELECT sc.geom, sc.stop_name, sc.stop_id, sc.platform_code, sc.stop_code,
                    sc.name_key, sc.cluster_id, a.route_code AS route_code_val, a.delay_sum, a.samples
                FROM agg_route_stop_daily a
                JOIN stop_clusters sc ON sc.stop_id = a.stop_id
                WHERE a.agency_id = $1 AND a.route_code = ANY($2) AND {agg_where}
            )
            {cluster_projection_sql}
            """,
            agency_id,
            list(ctx.routes),
            *params,
        )
    else:
        # No route filter → aggregate path (fast; reads from agg_stop_daily).
        agg_where, params, _ = build_agg_stop_filter(ctx, next_param=2)
        rows = await conn.fetch(
            f"""
            WITH {stop_clusters_cte},
            joined AS (
                SELECT sc.geom, sc.stop_name, sc.stop_id, sc.platform_code, sc.stop_code,
                    sc.name_key, sc.cluster_id, r.route_codes AS route_code_val, a.delay_sum, a.samples
                FROM agg_stop_daily a
                JOIN stop_clusters sc ON sc.stop_id = a.stop_id
                LEFT JOIN agg_stop_routes r ON r.agency_id = $1 AND r.stop_id = a.stop_id
                WHERE a.agency_id = $1 AND {agg_where}
            )
            {cluster_projection_sql}
            """,
            agency_id,
            *params,
        )

    fc = _heatmap_features(rows)
    fc["ctx"] = {
        "from": ctx.from_date.isoformat(),
        "to": ctx.to_date.isoformat(),
        "dow": ctx.dow,
        "time_band": ctx.time_band,
        "service": ctx.service,
        "routes": list(ctx.routes),
    }
    return fc
