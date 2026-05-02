from fastapi import APIRouter, Depends, Query

from api.deps import get_agency, get_conn

router = APIRouter(prefix="/api/{agency_id}", tags=["map"])


@router.get("/delays/live")
async def live_delays(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    limit: int = Query(default=100, le=500),
):
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (trip_id)
            trip_id, route_code, service_type, scheduled_time,
            dep_delay, captured_at
        FROM updates
        WHERE agency_id=$1 AND dep_delay IS NOT NULL
        ORDER BY trip_id, captured_at DESC
        LIMIT $2
        """,
        agency_id,
        limit,
    )
    return [dict(r) for r in rows]


@router.get("/delays/heatmap")
async def delay_heatmap(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    rows = await conn.fetch(
        """
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
        GROUP BY ss.stop_id, ss.stop_name, ss.geom
        """,
        agency_id,
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
    return {"type": "FeatureCollection", "features": features}
