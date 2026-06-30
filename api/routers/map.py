"""Map-tab endpoints.

Three resources back the Map tab:

- ``GET /delays/live``: rows from the most recent ``captured_at`` date.
- ``GET /route-shape``: ordered stop sequence for one route plus, when
  the agency has loaded GTFS ``shapes.txt``, a real road-shape
  ``geometry`` field. Falls back to ``geometry: null`` so the frontend
  can draw a stop-coordinate polyline as a graceful degrade.
- ``GET /delays/heatmap``: per-stop average delay GeoJSON, scoped by
  the user's range / DOW / time-band filter. Stops are clustered by
  ``stop_name`` plus a spatial bucket so inbound/outbound platforms of
  the same logical stop merge into one circle.

The heatmap and route-shape endpoints honor :class:`~api.range.RangeCtx`
so the displayed colors match what compute_ranking et al. show under
the same filter.
"""

import json

from fastapi import APIRouter, Depends, Query

from api.deps import get_agency, get_conn
from api.range import RangeCtx, build_agg_stop_filter, build_updates_filter, get_range_ctx
from api.triage import classify_route

router = APIRouter(prefix="/api/{agency_id}", tags=["map"])


@router.get("/delays/live")
async def live_delays(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    limit: int = Query(default=200, le=500),
):
    """Rows from the most recent observation date with a freshness header."""
    latest = await conn.fetchrow(
        "SELECT MAX(captured_at) AS ts FROM updates WHERE agency_id=$1",
        agency_id,
    )
    latest_ts = latest["ts"] if latest else None
    if latest_ts is None:
        return {"latest_captured_at": None, "rows": []}

    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (trip_id)
            trip_id, route_code, service_type, scheduled_time,
            dep_delay, captured_at
        FROM updates
        WHERE agency_id=$1
          AND dep_delay IS NOT NULL
          AND captured_at::date = $2::date
        ORDER BY trip_id, captured_at DESC
        LIMIT $3
        """,
        agency_id,
        latest_ts,
        limit,
    )
    return {
        "latest_captured_at": latest_ts.isoformat(),
        "rows": [dict(r) for r in rows],
    }


@router.get("/route-shape")
async def route_shape(
    route: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
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
    # Most-frequent shape_id for this route, bridged via updates.trip_id
    # (route_code is regex-extracted and is not guaranteed equal to GTFS
    # route_id across feeds, so joining on trip_id keeps geometry tied
    # to trips actually observed for this route_code). The chosen shape
    # also pins the stops query below so the polyline and circles share
    # one variant — without this pin, multi-shape routes (e.g. Hiroshima
    # express bus with several variants) showed stops off the line.
    shape_row = await conn.fetchrow(
        """
        SELECT t.shape_id, COUNT(*) AS n
        FROM static_trips t
        JOIN updates u
          ON u.agency_id = t.agency_id
         AND u.trip_id = t.trip_id
        WHERE t.agency_id = $1
          AND u.route_code = $2
          AND t.shape_id IS NOT NULL
          AND t.shape_id <> ''
        GROUP BY t.shape_id
        ORDER BY n DESC
        LIMIT 1
        """,
        agency_id,
        route,
    )
    chosen_shape_id = shape_row["shape_id"] if shape_row else None

    geometry = None
    if chosen_shape_id is not None:
        geom_row = await conn.fetchrow(
            "SELECT ST_AsGeoJSON(geom) AS geom_json FROM static_shapes WHERE agency_id = $1 AND shape_id = $2",
            agency_id,
            chosen_shape_id,
        )
        raw = geom_row["geom_json"] if geom_row else None
        geometry = json.loads(raw) if raw is not None else None

    # Honor full ctx (DOW / time_band / service / dates) so the polyline
    # colors match what compute_ranking et al. show for the same filters.
    # When a shape is chosen, restrict to trips on that shape so the stops
    # rendered align with the polyline; falls back to all-trips when the
    # route has no shape data at all.
    where_frag, params, next_param = build_updates_filter(ctx, next_param=3)
    if chosen_shape_id is not None:
        shape_filter = (
            f" AND trip_id IN (SELECT trip_id FROM static_trips "
            f"                  WHERE agency_id = $1 AND shape_id = ${next_param})"
        )
        params = [*params, chosen_shape_id]
    else:
        shape_filter = ""
    rows = await conn.fetch(
        f"""
        WITH dedup AS (
            SELECT DISTINCT ON (trip_id, stop_sequence)
                trip_id, stop_sequence, dep_delay
            FROM updates
            WHERE agency_id=$1 AND route_code=$2
              AND dep_delay IS NOT NULL
              AND {where_frag}
              {shape_filter}
            ORDER BY trip_id, stop_sequence, captured_at DESC
        )
        SELECT
            d.stop_sequence,
            COALESCE(MAX(ss.stop_name), d.stop_sequence::text || '番停留所') AS stop_name,
            MAX(ss.stop_id)       AS stop_id,
            MAX(ss.stop_code)     AS stop_code,
            MAX(ss.platform_code) AS platform_code,
            ROUND(AVG(d.dep_delay) / 60.0::numeric, 2) AS avg_min,
            COUNT(*) AS samples,
            AVG(ST_X(ss.geom)) AS lon,
            AVG(ST_Y(ss.geom)) AS lat
        FROM dedup d
        LEFT JOIN static_stop_times sst
          ON d.trip_id = sst.trip_id AND d.stop_sequence = sst.stop_sequence
          AND sst.agency_id = $1
        LEFT JOIN static_stops ss
          ON sst.stop_id = ss.stop_id AND ss.agency_id = $1
        GROUP BY d.stop_sequence
        ORDER BY d.stop_sequence
        """,
        agency_id,
        route,
        *params,
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
async def today_route_summary(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
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
    # not analyze recency — a cheap index-only MAX, independent of the agg.
    latest_ts = await conn.fetchval(
        "SELECT MAX(captured_at) FROM updates WHERE agency_id=$1",
        agency_id,
    )

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
async def route_trips(
    route_code: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Per-trip delay for one route on the latest observation date.

    One row per trip_id: representative scheduled departure (HH:MM), headsign
    (from static_trips), and the trip's average dep_delay across its stops.
    Sorted worst-first — answers "which buses were late". Read-only.
    """
    latest = await conn.fetchrow(
        "SELECT MAX(captured_at) AS ts FROM updates WHERE agency_id=$1 AND route_code=$2",
        agency_id,
        route_code,
    )
    latest_ts = latest["ts"] if latest else None
    if latest_ts is None:
        return {"date": None, "trips": []}

    rows = await conn.fetch(
        """
        WITH dedup AS (
            SELECT DISTINCT ON (trip_id, stop_sequence)
                trip_id, scheduled_time, dep_delay
            FROM updates
            WHERE agency_id=$1 AND route_code=$2
              AND dep_delay IS NOT NULL
              AND captured_at::date = $3::date
            ORDER BY trip_id, stop_sequence, captured_at DESC
        )
        SELECT
            d.trip_id,
            to_char(MIN(d.scheduled_time), 'HH24:MI') AS scheduled_time,
            MAX(t.trip_headsign) AS headsign,
            ROUND(AVG(d.dep_delay)::numeric, 0)::int AS avg_delay_sec,
            COUNT(*) AS samples
        FROM dedup d
        LEFT JOIN static_trips t
          ON t.agency_id = $1 AND t.trip_id = d.trip_id
        GROUP BY d.trip_id
        ORDER BY avg_delay_sec DESC NULLS LAST
        """,
        agency_id,
        route_code,
        latest_ts,
    )
    return {
        "date": latest_ts.date().isoformat(),
        "trips": [
            {
                "trip_id": r["trip_id"],
                "scheduled_time": r["scheduled_time"],
                "headsign": r["headsign"],
                "avg_delay_sec": r["avg_delay_sec"],
                "samples": r["samples"],
            }
            for r in rows
        ],
    }


@router.get("/today/route/{route_code}/stop-profile")
async def route_stop_profile(
    route_code: str,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Average delay per stop_sequence along one route on the latest date.

    Joins observed (trip_id, stop_sequence) to static_stops for a stop name,
    ordered by sequence — answers "where on the route does delay build". The
    name is best-effort (MAX over the sequence's mapped stop). Read-only.
    """
    latest = await conn.fetchrow(
        "SELECT MAX(captured_at) AS ts FROM updates WHERE agency_id=$1 AND route_code=$2",
        agency_id,
        route_code,
    )
    latest_ts = latest["ts"] if latest else None
    if latest_ts is None:
        return {"date": None, "stops": []}

    rows = await conn.fetch(
        """
        WITH dedup AS (
            SELECT DISTINCT ON (trip_id, stop_sequence)
                trip_id, stop_sequence, dep_delay
            FROM updates
            WHERE agency_id=$1 AND route_code=$2
              AND dep_delay IS NOT NULL
              AND captured_at::date = $3::date
            ORDER BY trip_id, stop_sequence, captured_at DESC
        )
        SELECT
            d.stop_sequence,
            MAX(ss.stop_name) AS stop_name,
            ROUND(AVG(d.dep_delay)::numeric, 0)::int AS avg_delay_sec,
            COUNT(*) AS samples
        FROM dedup d
        LEFT JOIN static_stop_times sst
          ON sst.agency_id = $1 AND sst.trip_id = d.trip_id AND sst.stop_sequence = d.stop_sequence
        LEFT JOIN static_stops ss
          ON ss.agency_id = $1 AND ss.stop_id = sst.stop_id
        GROUP BY d.stop_sequence
        ORDER BY d.stop_sequence
        """,
        agency_id,
        route_code,
        latest_ts,
    )
    return {
        "date": latest_ts.date().isoformat(),
        "stops": [
            {
                "stop_sequence": r["stop_sequence"],
                "stop_name": r["stop_name"],
                "avg_delay_sec": r["avg_delay_sec"],
                "samples": r["samples"],
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
async def delay_heatmap(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
):
    """Per-stop average delay GeoJSON, scoped to the request's range/DOW/time-band.

    Clustering: two physical platforms with the same ``stop_name`` within a
    ~5 km grid (``ST_SnapToGrid(geom, 0.05)`` ≈ 5.5 km lat × 4.2 km lon at
    lat 40°) collapse into one circle. Same name far apart stays separate
    because the spatial bucket changes. Stops without a ``stop_name`` fall
    back to spatial-only bucketing using ``stop_id`` as the synthetic key
    and a tighter ~1 km grid.

    Output coordinates are the centroid of the merged poles so the dot sits
    between paired platforms rather than on one of them.

    Served entirely from precomputed aggregates (no live ``updates`` scan): the
    no-route case reads ``agg_stop_daily``; a route filter reads
    ``agg_route_stop_daily`` (pre-split by ``route_code``). Both aggregates are
    deduped to one row per trip-stop event, so ``samples`` is an observation count.
    """
    if ctx.routes:
        # Route filter → aggregate path (agg_route_stop_daily is pre-split by route_code).
        # Mirrors the no-route branch's spatial grouping; adds a route_code = ANY($2)
        # filter. $1=agency_id, $2=route list, so the ctx filter starts at $3.
        agg_where, params, _ = build_agg_stop_filter(ctx, next_param=3)
        rows = await conn.fetch(
            f"""
            SELECT
                AVG(ST_X(ss.geom))::numeric AS lon,
                AVG(ST_Y(ss.geom))::numeric AS lat,
                string_agg(DISTINCT ss.stop_name, ' / ' ORDER BY ss.stop_name) AS stop_name,
                string_agg(DISTINCT ss.stop_id, ',') AS stop_ids,
                string_agg(DISTINCT NULLIF(ss.platform_code, ''), ',' ORDER BY NULLIF(ss.platform_code, ''))
                    AS platform_codes,
                string_agg(DISTINCT NULLIF(ss.stop_code, ''), ' / ' ORDER BY NULLIF(ss.stop_code, ''))
                    AS stop_codes,
                string_agg(DISTINCT a.route_code, ',' ORDER BY a.route_code) AS route_codes,
                ROUND(SUM(a.delay_sum)::numeric / SUM(a.samples) / 60.0, 2) AS avg_delay_min,
                ROUND(
                    PERCENTILE_CONT(0.9) WITHIN GROUP (
                        ORDER BY a.delay_sum::float / NULLIF(a.samples, 0)
                    )::numeric / 60.0,
                2) AS p90_delay_min,
                SUM(a.samples) AS samples
            FROM agg_route_stop_daily a
            JOIN static_stops ss ON ss.agency_id = $1 AND ss.stop_id = a.stop_id
            WHERE a.agency_id = $1 AND a.route_code = ANY($2) AND ss.geom IS NOT NULL
                AND {agg_where}
            GROUP BY
                CASE
                    WHEN COALESCE(NULLIF(ss.stop_name, ''), '') <> ''
                        THEN ss.stop_name
                    ELSE 'unnamed:' || ss.stop_id
                END,
                CASE
                    WHEN COALESCE(NULLIF(ss.stop_name, ''), '') <> ''
                        THEN ST_SnapToGrid(ss.geom, 0.05)
                    ELSE ST_SnapToGrid(ss.geom, 0.01)
                END
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
            SELECT
                AVG(ST_X(ss.geom))::numeric AS lon,
                AVG(ST_Y(ss.geom))::numeric AS lat,
                string_agg(DISTINCT ss.stop_name, ' / ' ORDER BY ss.stop_name) AS stop_name,
                string_agg(DISTINCT ss.stop_id, ',') AS stop_ids,
                string_agg(DISTINCT NULLIF(ss.platform_code, ''), ',' ORDER BY NULLIF(ss.platform_code, ''))
                    AS platform_codes,
                string_agg(DISTINCT NULLIF(ss.stop_code, ''), ' / ' ORDER BY NULLIF(ss.stop_code, ''))
                    AS stop_codes,
                string_agg(DISTINCT r.route_codes, ',') AS route_codes,
                ROUND(SUM(a.delay_sum)::numeric / SUM(a.samples) / 60.0, 2) AS avg_delay_min,
                ROUND(
                    PERCENTILE_CONT(0.9) WITHIN GROUP (
                        ORDER BY a.delay_sum::float / NULLIF(a.samples, 0)
                    )::numeric / 60.0,
                2) AS p90_delay_min,
                SUM(a.samples) AS samples
            FROM agg_stop_daily a
            JOIN static_stops ss ON ss.agency_id = $1 AND ss.stop_id = a.stop_id
            LEFT JOIN agg_stop_routes r ON r.agency_id = $1 AND r.stop_id = a.stop_id
            WHERE a.agency_id = $1 AND ss.geom IS NOT NULL AND {agg_where}
            GROUP BY
                CASE
                    WHEN COALESCE(NULLIF(ss.stop_name, ''), '') <> ''
                        THEN ss.stop_name
                    ELSE 'unnamed:' || ss.stop_id
                END,
                CASE
                    WHEN COALESCE(NULLIF(ss.stop_name, ''), '') <> ''
                        THEN ST_SnapToGrid(ss.geom, 0.05)
                    ELSE ST_SnapToGrid(ss.geom, 0.01)
                END
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
