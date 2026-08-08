"""Map-tab endpoints.

Three resources back the Map tab:

- ``GET /delays/live``: rows from the most recent ``captured_at`` date.
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

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from api.clickhouse import max_captured_at
from api.deps import get_agency, get_ch, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, build_agg_stop_filter, build_updates_filter_ch, get_range_ctx
from api.triage import classify_route

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


@router.get("/delays/live")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def live_delays(
    request: Request,
    agency_id: int = Depends(get_agency),
    ch=Depends(get_ch),
    limit: int = Query(default=200, le=500),
):
    """Rows from the most recent observation date with a freshness header."""
    latest_ts = await max_captured_at(ch, agency_id)
    if latest_ts is None:
        return {"latest_captured_at": None, "rows": []}

    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring),
    # matching the mechanism used at the other 3 sort-based-dedup sites in
    # this file (route_shape, route_trips, route_stop_profile). Unlike those,
    # this query is always bounded to one JST day off the sort index (measured
    # ~1s on real data, nowhere near the 30s max_execution_time cap) — the old
    # `ORDER BY ... LIMIT 1 BY` form was never a timeout risk here. The reason
    # to rewrite it anyway is consistency (one dedup idiom across the file,
    # not two) and determinism, per the tiebreak note below. Multiple non-key
    # columns (route_code, service_type, scheduled_time, dep_delay) are read
    # off the SAME winning row, so they're packed into ONE tuple-argMax rather
    # than one argMax per column — per-column argMax on a captured_at tie
    # could silently mix columns from two different physical rows. The inner
    # query's tuple is unpacked by position in the outer SELECT so the
    # result's column names/order match the pre-migration SELECT list
    # exactly (`route_code, service_type, scheduled_time, dep_delay`).
    #
    # This endpoint dedups by trip_id ALONE (no stop_sequence in the group
    # key), unlike build_dedup_ch_sql. A single GTFS-RT poll commonly reports
    # dep_delay for several of a trip's upcoming stops at once (confirmed on
    # real data: ~13% of trips on a given day), so more than one physical row
    # can share the exact same (captured_at, file_name) for one trip_id — a
    # tie the old sort-based form also never broke (ORDER BY trip_id,
    # captured_at DESC had no third key either), leaving its winner among
    # those rows to whatever order the query engine happened to produce.
    # `-toInt32(u.stop_sequence)` makes that residual tie deterministic here:
    # among same-poll rows for one trip, the one for the LOWEST stop_sequence
    # (the soonest upcoming stop) wins — the most currently-relevant row for a
    # live board. `scheduled_time` is the field this tie *most commonly*
    # touches (it's per-stop, so it differs across a trip's stop_sequence rows
    # whenever the poll spans multiple stops) — but `dep_delay` is ALSO
    # per-stop and can differ across those tied rows too: measured on real
    # data, a handful of tied trips (4/1245 on one agency, 7/266 on another)
    # had a different dep_delay between stop_sequences, by as much as a few
    # hundred seconds. route_code/service_type are trip-level and unaffected.
    rows_result = await ch.query(
        """
        SELECT trip_id, winner.1 AS route_code, winner.2 AS service_type,
            winner.3 AS scheduled_time, winner.4 AS dep_delay, captured_at
        FROM (
            SELECT u.trip_id AS trip_id,
                argMax(
                    tuple(u.route_code, u.service_type, u.scheduled_time, u.dep_delay),
                    (u.captured_at, u.file_name, -toInt32(u.stop_sequence))
                ) AS winner,
                max(u.captured_at) AS captured_at
            FROM updates AS u
            WHERE u.agency_id = {agency_id:UInt16}
              AND u.dep_delay IS NOT NULL
              AND toDate(u.captured_at, 'Asia/Tokyo') = toDate({latest_ts:DateTime64}, 'Asia/Tokyo')
            GROUP BY u.trip_id
        ) AS grouped
        ORDER BY trip_id
        LIMIT {limit:UInt32}
        """,
        parameters={"agency_id": agency_id, "latest_ts": latest_ts, "limit": limit},
    )
    # Build each row via dict(zip(...)) rather than deriving a column index
    # up front (e.g. `cols.index("captured_at")`): clickhouse-connect returns
    # `column_names == ()` for a zero-row result (routine here — an agency's
    # latest JST day can have observations where every one has a NULL
    # dep_delay, e.g. arrival-only rows or a degraded poll, in which case
    # `latest_ts` above is non-None but this query's `dep_delay IS NOT NULL`
    # filter matches zero rows), and `().index(...)` raises `ValueError`
    # unconditionally, before the loop even runs. Looping over `zip(...)`
    # instead just doesn't execute when `result_rows` is empty, so `[]` falls
    # out naturally.
    out_rows = []
    for r in rows_result.result_rows:
        row = dict(zip(rows_result.column_names, r, strict=True))
        row["captured_at"] = _as_utc(row["captured_at"])
        out_rows.append(row)
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

    Returns ``{ stops: [{ stop_sequence, stop_name, stop_id, stop_code,
    platform_code, lon, lat, avg_min, samples }], route }``. Powers the
    Map tab's per-route overlay (polyline + numbered stops) when the user
    filters to a single route. Sorted by ``stop_sequence`` so the frontend
    can draw a polyline directly from the result. Stops without coordinates
    are dropped so the polyline never includes (NaN, NaN).

    The optional GTFS identifiers (``stop_id`` / ``stop_code`` /
    ``platform_code``) are included so the unified popup template renders
    the same fields it shows for the heatmap layer — without them route
    mode would silently drop the pole badge and stop_id footer.
    """
    # Cheap Postgres existence precheck FIRST, before touching ClickHouse at
    # all -- ahead of the ctx-bounded dedup query below, not just inside the
    # empty-window fallback branch further down. A fabricated route_code must
    # cost ~0 ClickHouse work: measured on real data, with the precheck
    # misplaced inside the fallback branch, a fabricated route_code under a
    # wide ctx window still cost 336,368,237 rows / 923 MiB / 2.35s, because
    # the ctx-bounded dedup query ran to completion first regardless (it's
    # bounded, but still real, unnecessary work for a route that doesn't
    # exist at all).
    #
    # Checks `agg_route_daily`, not `agg_route_stats`: agg_route_stats is
    # built with `HAVING COUNT(*) > 20` and `WHERE service_type IS NOT NULL`
    # (pipeline/analyze.py), so it's a LOSSY existence oracle -- a real,
    # legitimately-observed route with <=20 lifetime deduped samples or an
    # all-NULL service_type is invisible to it even though
    # today_route_summary's route list (built from agg_route_daily, no such
    # filter) would show it with bucket="no_baseline". Checking
    # agg_route_daily instead matches the grain of the table that actually
    # populates the route list users click through from. No secondary index
    # on route_code (agg_route_daily's PK leads with (agency_id, date)), but
    # the table holds per-agency route×day×service rows, not raw `updates` --
    # measured ~1ms on real data even for agency 8's ~14k rows, for both a
    # fabricated and a real route_code. Accepted trade-off: a brand-new route
    # that's been ingested but not yet analyzed (no agg_route_daily row yet)
    # renders with no shape for one cron cycle -- the same trade-off
    # today_route_summary already accepts elsewhere in this file.
    route_exists = await conn.fetchval(
        "SELECT 1 FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2 LIMIT 1",
        agency_id,
        str(route),
    )
    if route_exists is None:
        return {"route": route, "geometry": None, "stops": [], "unobserved_stops": []}

    # Most-frequent shape_id for this route, bridged via updates.trip_id
    # (route_code is regex-extracted and is not guaranteed equal to GTFS
    # route_id across feeds, so joining on trip_id keeps geometry tied
    # to trips actually observed for this route_code). The chosen shape
    # also pins the stops query below so the polyline and circles share
    # one variant — without this pin, multi-shape routes (e.g. Hiroshima
    # express bus with several variants) showed stops off the line.
    #
    # `updates` now lives in ClickHouse. The shape-vote and the per-stop
    # delay dedup both scan the identical agency/route/ctx-bounded slice of
    # `updates`, so run the dedup query FIRST — WITHOUT any shape filter,
    # since chosen_shape_id isn't known yet — and derive the shape-vote's
    # per-trip counts from its own deduped rows in Python instead of paying
    # for a second ClickHouse scan. "Which trips appear, and how many
    # deduped stop-events each contributes" is a valid (arguably better)
    # proxy for shape-vote weight than the raw per-trip observation count,
    # since both are counted off the same dedup set.
    #
    # Trade-off (deliberate, not proven bit-for-bit equivalent to the old
    # two-query version): `dedup_rows` is filtered by `dep_delay IS NOT
    # NULL` below, so a trip whose every observed StopTimeUpdate is
    # arrival-only (no `dep_delay` — common at a route's last stop in
    # GTFS-RT) contributes ZERO weight to the vote here, where the old raw
    # `COUNT(*)` query counted it in full. We accept this: it reuses the
    # dedup scan's own established definition of "counted observation" (see
    # `pipeline/db.py::build_dedup_ch_sql` for the same filter-before-dedup
    # ordering elsewhere in this codebase) rather than inventing a second,
    # looser one just for the vote. A route where every trip on one shape
    # variant happens to be arrival-only everywhere could in principle tip
    # the vote toward a less-observed variant — accepted as a corner case,
    # not chased further; see the regression test asserting the vote stays
    # sensible (lands on the shape with real weighted support) when some
    # trips are entirely NULL-delay.
    #
    # Bounded by the same `ctx`-derived filter (date range / DOW / time_band
    # / service) honored by every other analytical endpoint — an earlier
    # version scanned the route's ENTIRE history here with no date bound
    # (measured 32.1s on agency 8's real data for one route, returning only
    # ~100 rows), even though the shape should reflect what's actually being
    # shown for the user's selected range, not all-time history.
    ch_where_frag, ch_params = build_updates_filter_ch(ctx)
    # argMax-based dedup (see pipeline/db.py::build_dedup_ch_sql's docstring) —
    # only one non-key column (dep_delay) is read off the winning row, so a
    # single argMax suffices; base-table columns are qualified with the `u.`
    # alias per that same docstring's convention, in case ch_where_frag (built
    # by api.range.build_updates_filter_ch) ever references an output alias.
    # `ORDER BY u.trip_id, u.stop_sequence` (matching route_trips' equivalent
    # dedup query) makes the row order deterministic: a bare GROUP BY has no
    # defined output order, and `lon`/`lat` below are float means accumulated
    # by summing `dedup_rows` in whatever order they arrive — without a fixed
    # order, floating-point summation is order-dependent and could produce a
    # last-bit-different average across otherwise-identical requests.
    dedup_result = await ch.query(
        f"""
        SELECT u.trip_id, u.stop_sequence,
            argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay
        FROM updates AS u
        WHERE u.agency_id = {{agency_id:UInt16}} AND u.route_code = {{route:String}}
          AND u.dep_delay IS NOT NULL
          AND {ch_where_frag}
        GROUP BY u.trip_id, u.stop_sequence
        ORDER BY u.trip_id, u.stop_sequence
        """,
        parameters={"agency_id": agency_id, "route": str(route), **ch_params},
    )
    dedup_rows: list[tuple[str, int, int]] = list(dedup_result.result_rows)
    trip_counts: dict[str, int] = dict(Counter(tid for tid, _, _ in dedup_rows))

    # If the ctx window has zero observations for this route (e.g. it only
    # runs on days outside the selected range, or a time_band excludes every
    # one of its trips), `dedup_rows`/`trip_counts` above come back empty —
    # there's no shape-vote signal to derive from them, but the map should
    # still be able to render the route's topology (geometry +
    # unobserved-stop markers), just with no delay data on it, matching
    # pre-ClickHouse-migration behavior. Run ONE fallback shape-vote query
    # (bounded to the last 30 days off the agency's own latest data — see
    # below), solely to pick a shape for rendering purposes — this only
    # fires on the empty-window edge case (not the common case), so even
    # its now-bounded form doesn't reintroduce the 32s-per-request problem
    # the ctx bound above exists to fix. The per-stop delay stats
    # (`avg_min`/`samples`) stay empty regardless, since there really are
    # zero observations in the user's selected window.
    if not trip_counts:
        # Existence is already confirmed by the precheck at the top of this
        # function, so this bound only needs to cap the cost for a route
        # that's real but has nothing in the ctx window: 30 days off the
        # agency's own latest captured_at (not wall-clock "now") so it's
        # meaningful against old/replayed data too, matching the uniform
        # bound used by route_trips/route_stop_profile below.
        agency_latest = await max_captured_at(ch, agency_id)
        if agency_latest is not None:
            fallback_bound = agency_latest - timedelta(days=30)
            fallback_vote_result = await ch.query(
                "SELECT trip_id, count() AS n FROM updates "
                "WHERE agency_id = {agency_id:UInt16} AND route_code = {route:String} "
                "  AND captured_at >= {bound:DateTime64} "
                "GROUP BY trip_id",
                parameters={"agency_id": agency_id, "route": str(route), "bound": fallback_bound},
            )
            trip_counts = {tid: n for tid, n in fallback_vote_result.result_rows}

    chosen_shape_id = None
    if trip_counts:
        shape_link_rows = await conn.fetch(
            "SELECT trip_id, shape_id FROM static_trips "
            "WHERE agency_id = $1 AND trip_id = ANY($2) "
            "  AND shape_id IS NOT NULL AND shape_id <> ''",
            agency_id,
            list(trip_counts.keys()),
        )
        shape_counts: dict[str, int] = defaultdict(int)
        for r in shape_link_rows:
            shape_counts[r["shape_id"]] += trip_counts.get(r["trip_id"], 0)
        if shape_counts:
            # Explicit tie-break key: `shape_counts` is a `defaultdict`
            # populated from an unordered Postgres query, so two shape
            # variants tied on vote count would otherwise pick whichever
            # happened to be inserted first that run — non-deterministic
            # polyline/unobserved_stops between identical requests. Same bug
            # class already fixed for movers ranking in overview.py.
            chosen_shape_id = max(shape_counts, key=lambda sid: (shape_counts[sid], sid))

    geometry = None
    if chosen_shape_id is not None:
        geom_row = await conn.fetchrow(
            "SELECT ST_AsGeoJSON(geom) AS geom_json FROM static_shapes WHERE agency_id = $1 AND shape_id = $2",
            agency_id,
            chosen_shape_id,
        )
        raw = geom_row["geom_json"] if geom_row else None
        geometry = json.loads(raw) if raw is not None else None

    # When a shape is chosen, restrict `dedup_rows` to trips on that shape
    # (in Python — `dedup_rows` already carries every ctx-bounded trip for
    # this route, filtering it a second time via ClickHouse would be another
    # redundant scan) so the per-stop delay stats rendered align with the
    # polyline; falls back to all-trips when the route has no shape data at
    # all. In the empty-ctx-window fallback case above, `dedup_rows` is
    # already empty, so this filter is a no-op and `stops` stays empty while
    # `geometry`/`unobserved_stops` still render from the fallback shape.
    shape_trip_ids: list[str] | None = None
    if chosen_shape_id is not None:
        shape_trip_rows = await conn.fetch(
            "SELECT trip_id FROM static_trips WHERE agency_id = $1 AND shape_id = $2",
            agency_id,
            chosen_shape_id,
        )
        shape_trip_ids = [r["trip_id"] for r in shape_trip_rows]
        shape_trip_id_set = set(shape_trip_ids)
        dedup_rows = [row for row in dedup_rows if row[0] in shape_trip_id_set]

    static_join_rows: list = []
    if dedup_rows:
        dedup_trip_ids = list({tid for tid, _, _ in dedup_rows})
        static_join_rows = await conn.fetch(
            "SELECT sst.trip_id, sst.stop_sequence, ss.stop_id, ss.stop_name, "
            "       ss.stop_code, ss.platform_code, ST_X(ss.geom) AS lon, ST_Y(ss.geom) AS lat "
            "FROM static_stop_times sst "
            "LEFT JOIN static_stops ss ON sst.stop_id = ss.stop_id AND ss.agency_id = $1 "
            "WHERE sst.agency_id = $1 AND sst.trip_id = ANY($2)",
            agency_id,
            dedup_trip_ids,
        )
    static_by_pair = {(r["trip_id"], r["stop_sequence"]): r for r in static_join_rows}

    def _round2(x) -> float:
        return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _new_seq_agg() -> dict:
        return {
            "delays": [],
            "stop_names": [],
            "stop_ids": [],
            "stop_codes": [],
            "platform_codes": [],
            "lons": [],
            "lats": [],
        }

    per_seq: dict[int, dict] = defaultdict(_new_seq_agg)
    for trip_id, stop_sequence, dep_delay in dedup_rows:
        a = per_seq[stop_sequence]
        a["delays"].append(dep_delay)
        info = static_by_pair.get((trip_id, stop_sequence))
        if info is not None:
            if info["stop_name"] is not None:
                a["stop_names"].append(info["stop_name"])
            if info["stop_id"] is not None:
                a["stop_ids"].append(info["stop_id"])
            if info["stop_code"] is not None:
                a["stop_codes"].append(info["stop_code"])
            if info["platform_code"] is not None:
                a["platform_codes"].append(info["platform_code"])
            if info["lon"] is not None:
                a["lons"].append(float(info["lon"]))
            if info["lat"] is not None:
                a["lats"].append(float(info["lat"]))

    rows = []
    for stop_sequence in sorted(per_seq):
        a = per_seq[stop_sequence]
        rows.append(
            {
                "stop_sequence": stop_sequence,
                "stop_name": max(a["stop_names"]) if a["stop_names"] else f"{stop_sequence}番停留所",
                "stop_id": max(a["stop_ids"]) if a["stop_ids"] else None,
                "stop_code": max(a["stop_codes"]) if a["stop_codes"] else None,
                "platform_code": max(a["platform_codes"]) if a["platform_codes"] else None,
                "avg_min": _round2(sum(a["delays"]) / len(a["delays"]) / 60.0) if a["delays"] else None,
                "samples": len(a["delays"]),
                "lon": sum(a["lons"]) / len(a["lons"]) if a["lons"] else None,
                "lat": sum(a["lats"]) / len(a["lats"]) if a["lats"] else None,
            }
        )
    observed_seqs = {r["stop_sequence"] for r in rows}

    # Unobserved stops on the chosen shape: every (stop_sequence, stop)
    # tuple from static_stop_times for trips on the chosen shape, minus
    # the sequences already returned with delay data. Hollow markers in
    # the frontend so the user can see the full route topology and the
    # observation gap (typical for Hiroshima-style incremental feeds
    # where early-trip sequences are rarely caught by 30s polling).
    unobserved = []
    if chosen_shape_id is not None:
        unobs_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (sst.stop_sequence)
                sst.stop_sequence,
                ss.stop_name,
                ss.stop_id,
                ss.stop_code,
                ss.platform_code,
                ST_X(ss.geom) AS lon,
                ST_Y(ss.geom) AS lat
            FROM static_trips t
            JOIN static_stop_times sst
              ON sst.agency_id = t.agency_id AND sst.trip_id = t.trip_id
            JOIN static_stops ss
              ON ss.agency_id = sst.agency_id AND ss.stop_id = sst.stop_id
            WHERE t.agency_id = $1 AND t.shape_id = $2
            ORDER BY sst.stop_sequence, sst.trip_id
            """,
            agency_id,
            chosen_shape_id,
        )
        unobserved = [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_name": r["stop_name"],
                "stop_id": r["stop_id"],
                "stop_code": r["stop_code"],
                "platform_code": r["platform_code"],
                "lon": float(r["lon"]) if r["lon"] is not None else None,
                "lat": float(r["lat"]) if r["lat"] is not None else None,
            }
            for r in unobs_rows
            if r["stop_sequence"] not in observed_seqs and r["lon"] is not None and r["lat"] is not None
        ]

    return {
        "route": route,
        "geometry": geometry,
        "stops": [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_name": r["stop_name"],
                "stop_id": r["stop_id"],
                "stop_code": r["stop_code"],
                "platform_code": r["platform_code"],
                "lon": float(r["lon"]) if r["lon"] is not None else None,
                "lat": float(r["lat"]) if r["lat"] is not None else None,
                "avg_min": float(r["avg_min"]) if r["avg_min"] is not None else None,
                "samples": r["samples"],
            }
            for r in rows
            if r["lon"] is not None and r["lat"] is not None
        ],
        "unobserved_stops": unobserved,
    }


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
    (today vs baseline), and a ``low_confidence`` flag for thin samples. The
    client groups by bucket, so the SQL ``ORDER BY`` is only a sensible default.

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
            -- has no '' row. Mirrors the digest's route-grain baseline.
            SELECT route_code,
                   SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS base_avg_min,
                   SUM(p90_min * samples) / NULLIF(SUM(samples), 0) AS base_p90_min
            FROM agg_route_stats
            WHERE agency_id = $1 AND samples IS NOT NULL
            GROUP BY route_code
        )
        SELECT
            d.route_code, d.service_type, d.avg_delay_sec, d.worst_delay_sec,
            d.trips_observed, d.samples, d.last_seen_at,
            COALESCE(b.avg_min, rb.base_avg_min) AS baseline_avg_min,
            COALESCE(b.p90_min, rb.base_p90_min) AS baseline_p90_min,
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
                "deviation_sec": deviation_sec,
                "bucket": bucket,
                "low_confidence": low_confidence,
                "has_baseline": baseline_avg_sec is not None,
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
    # Cheap Postgres existence precheck FIRST, before touching ClickHouse at
    # all: a fabricated/nonexistent route_code on this anonymous, reachable
    # endpoint must cost ~0 ClickHouse work, not just a bounded-but-still-huge
    # scan (measured: a date bound alone still read ~170M rows here, since no
    # agency yet has more than ~130 days of history for the bound to actually
    # exclude).
    #
    # Checks `agg_route_daily`, not `agg_route_stats`: agg_route_stats is
    # built with `HAVING COUNT(*) > 20` and `WHERE service_type IS NOT NULL`
    # (pipeline/analyze.py), so it's a LOSSY existence oracle -- a real,
    # legitimately-observed route with <=20 lifetime deduped samples or an
    # all-NULL service_type is invisible to it even though
    # today_route_summary's route list (built from agg_route_daily, no such
    # filter) would show it with bucket="no_baseline". Checking
    # agg_route_daily instead matches the grain of the table that actually
    # populates the route list users click through from. No secondary index
    # on route_code (agg_route_daily's PK leads with (agency_id, date)), but
    # the table holds per-agency route×day×service rows, not raw `updates` --
    # measured ~1ms on real data even for agency 8's ~14k rows, for both a
    # fabricated and a real route_code. Accepted trade-off: a brand-new route
    # that's been ingested but not yet analyzed (no agg_route_daily row yet)
    # reads as "not found" for one cron cycle -- the same trade-off
    # today_route_summary already accepts elsewhere in this file.
    route_exists = await conn.fetchval(
        "SELECT 1 FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2 LIMIT 1",
        agency_id,
        route_code,
    )
    if route_exists is None:
        return {"date": None, "trips": []}

    # Resolve the agency's own latest captured_at (agency_id is the sort
    # key's leading column, so this is index-served regardless of route
    # activity — see api.clickhouse.max_captured_at's docstring), then bound
    # the route-scoped probe below to the last 30 days from it. Now that the
    # existence precheck above rules out fabricated route_codes, this bound
    # only needs to cap the cost for a REAL route with no recent data — a
    # literal Python-computed value (not a ClickHouse scalar subquery —
    # measured to read MORE rows, not fewer, since it isn't servable as a
    # sort-key literal), anchored to the agency's own data rather than
    # wall-clock "now" so it's meaningful against replayed/old data too. A
    # route with zero observations in the last 30 days still correctly falls
    # through to the empty response below — for a route active more than 30
    # days ago, this is arguably more correct than before (which would
    # resurrect arbitrarily ancient data).
    agency_latest = await max_captured_at(ch, agency_id)
    if agency_latest is None:
        return {"date": None, "trips": []}
    route_probe_bound = agency_latest - timedelta(days=30)
    latest_result = await ch.query(
        "SELECT captured_at FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND route_code = {route:String} "
        "  AND captured_at >= {bound:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "route": route_code, "bound": route_probe_bound},
    )
    latest_ts = _as_utc(latest_result.result_rows[0][0] if latest_result.result_rows else None)
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
    """Merge cohort stats for one stop into the stop dict."""
    if stop_id is None or stop_id not in cohort:
        return {"cohort_avg_delay_sec": None, "cohort_route_count": 0, "is_outlier": False}
    c = cohort[stop_id]
    cohort_avg = c["cohort_avg_delay_sec"]
    route_count = c["cohort_route_count"]
    is_outlier = cohort_avg is not None and route_count >= 2 and route_avg_sec > cohort_avg * 1.5
    return {
        "cohort_avg_delay_sec": cohort_avg,
        "cohort_route_count": route_count,
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
    # Existence precheck (against agg_route_daily, not agg_route_stats — see
    # route_trips above for why) + bounded route-scoped probe — see
    # route_trips above for the full rationale (same fabricated-route-code /
    # unbounded-scan vulnerability, same fix).
    route_exists = await conn.fetchval(
        "SELECT 1 FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2 LIMIT 1",
        agency_id,
        route_code,
    )
    if route_exists is None:
        return {"date": None, "stops": []}

    agency_latest = await max_captured_at(ch, agency_id)
    if agency_latest is None:
        return {"date": None, "stops": []}
    route_probe_bound = agency_latest - timedelta(days=30)
    latest_result = await ch.query(
        "SELECT captured_at FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND route_code = {route:String} "
        "  AND captured_at >= {bound:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "route": route_code, "bound": route_probe_bound},
    )
    latest_ts = _as_utc(latest_result.result_rows[0][0] if latest_result.result_rows else None)
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
                ROUND(
                    AVG(delay_sum::float / NULLIF(samples, 0))::numeric, 0
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
    platform_code, avg_delay_min, p90_delay_min, samples, route_codes).
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
    # *stop* instead of per (stop, date, time_band) agg row it joins to
    # measured ~4x faster on real data (429ms vs 1.86s for one agency-month).
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
