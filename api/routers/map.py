"""Map-tab endpoints: live (= latest observation) delays and the heatmap.

The heatmap honors the global :class:`~api.range.RangeCtx` so the user's
chosen time / DOW / time-band filter applies. The "live" endpoint serves
rows from the most recent ``captured_at`` date in the table — adapts to
whatever ingest path the operator runs.
"""

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


@router.get("/delays/heatmap")
async def delay_heatmap(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
):
    """Per-stop average delay GeoJSON, scoped to the request's range/DOW/time-band."""
    where_frag, params, _ = build_updates_filter(ctx, next_param=2)
    rows = await conn.fetch(
        f"""
        SELECT
            ss.stop_id, ss.stop_name,
            ST_X(ss.geom) AS lon, ST_Y(ss.geom) AS lat,
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
        GROUP BY ss.stop_id, ss.stop_name, ss.geom
        """,
        agency_id,
        *params,
    )
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": {
                "stop_id": r["stop_id"],
                "stop_name": r["stop_name"],
                "avg_delay_min": float(r["avg_delay_min"]),
                "samples": r["samples"],
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
