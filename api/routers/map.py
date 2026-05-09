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
from api.range import RangeCtx, build_updates_filter, get_range_ctx

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
    # to trips actually observed for this route_code).
    geom_row = await conn.fetchrow(
        """
        WITH ranked AS (
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
        )
        SELECT ST_AsGeoJSON(s.geom) AS geom_json
        FROM ranked r
        JOIN static_shapes s
          ON s.agency_id = $1 AND s.shape_id = r.shape_id
        """,
        agency_id,
        route,
    )
    raw = geom_row["geom_json"] if geom_row else None
    geometry = json.loads(raw) if raw is not None else None

    # Honor full ctx (DOW / time_band / service / dates) so the polyline
    # colors match what compute_ranking et al. show for the same filters.
    where_frag, params, _ = build_updates_filter(ctx, next_param=3)
    rows = await conn.fetch(
        f"""
        WITH dedup AS (
            SELECT DISTINCT ON (trip_id, stop_sequence)
                trip_id, stop_sequence, dep_delay
            FROM updates
            WHERE agency_id=$1 AND route_code=$2
              AND dep_delay IS NOT NULL
              AND {where_frag}
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
    }


@router.get("/today/route-summary")
async def today_route_summary(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Per-route operational summary for the most recent observation date.

    Powers the 最新観測 tab. Each row is one route_code with:
    - avg_delay_sec, worst_delay_sec, trips_observed, last_seen_at, service_type
    Sorted by worst delay descending so problem routes float to the top.
    """
    latest = await conn.fetchrow(
        "SELECT MAX(captured_at) AS ts FROM updates WHERE agency_id=$1",
        agency_id,
    )
    latest_ts = latest["ts"] if latest else None
    if latest_ts is None:
        return {"latest_captured_at": None, "date": None, "routes": []}

    rows = await conn.fetch(
        """
        WITH dedup AS (
            SELECT DISTINCT ON (trip_id, stop_sequence)
                trip_id, route_code, service_type, dep_delay, captured_at
            FROM updates
            WHERE agency_id=$1
              AND dep_delay IS NOT NULL
              AND captured_at::date = $2::date
            ORDER BY trip_id, stop_sequence, captured_at DESC
        )
        SELECT
            route_code,
            service_type,
            ROUND(AVG(dep_delay)::numeric, 0)::int AS avg_delay_sec,
            MAX(dep_delay) AS worst_delay_sec,
            COUNT(DISTINCT trip_id) AS trips_observed,
            COUNT(*) AS samples,
            MAX(captured_at) AS last_seen_at
        FROM dedup
        GROUP BY route_code, service_type
        ORDER BY worst_delay_sec DESC NULLS LAST
        """,
        agency_id,
        latest_ts,
    )
    return {
        "latest_captured_at": latest_ts.isoformat(),
        "date": latest_ts.date().isoformat(),
        "routes": [
            {
                "route_code": r["route_code"],
                "service_type": r["service_type"],
                "avg_delay_sec": r["avg_delay_sec"],
                "worst_delay_sec": r["worst_delay_sec"],
                "trips_observed": r["trips_observed"],
                "samples": r["samples"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
            }
            for r in rows
        ],
    }


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
    """
    where_frag, params, _ = build_updates_filter(ctx, next_param=2)
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
            string_agg(DISTINCT u.route_code, ',' ORDER BY u.route_code) AS route_codes,
            ROUND(AVG(u.dep_delay) / 60.0::numeric, 2) AS avg_delay_min,
            COUNT(*) AS samples
        FROM updates u
        JOIN static_stop_times sst
            ON u.trip_id = sst.trip_id AND u.stop_sequence = sst.stop_sequence
            AND sst.agency_id = $1
        JOIN static_stops ss
            ON sst.stop_id = ss.stop_id AND ss.agency_id = $1
        WHERE u.agency_id = $1
            AND u.dep_delay IS NOT NULL
            AND ss.geom IS NOT NULL
            AND {where_frag}
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
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(r["lon"]), 6), round(float(r["lat"]), 6)]},
            "properties": {
                "stop_id": r["stop_ids"],  # comma-joined list when clustered
                "stop_name": r["stop_name"],
                "stop_code": r["stop_codes"] or "",
                "platform_code": r["platform_codes"] or "",
                "avg_delay_min": float(r["avg_delay_min"]),
                "samples": r["samples"],
                "route_codes": r["route_codes"] or "",
            },
        }
        for r in rows
        if r["lon"] is not None and r["lat"] is not None
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "ctx": {
            "from": ctx.from_date.isoformat(),
            "to": ctx.to_date.isoformat(),
            "dow": ctx.dow,
            "time_band": ctx.time_band,
            "service": ctx.service,
            "routes": list(ctx.routes),
        },
    }
