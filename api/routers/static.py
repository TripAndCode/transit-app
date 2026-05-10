from fastapi import APIRouter, Depends

from api.deps import get_agency, get_conn

router = APIRouter(prefix="/api/{agency_id}", tags=["static"])


@router.get("/routes")
async def list_routes(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """List of static routes plus the numeric ``route_code`` used by updates.

    For Aomori (``aomori_regex`` ingest) the trip-id-derived ``route_code``
    (e.g. '1021') is extracted from the parenthesised tail of ``route_id``
    (e.g. '国道・古川線(1021)'). For Hiroshima (``static_join`` ingest) the
    ``route_id`` itself is the ``route_code``, and the ``regexp_replace`` is
    a no-op for those purely numeric ids.

    ``route_long_name`` is included so the frontend can show a human label
    (e.g. '5号線', '呉倉橋島線') instead of the keito short code alone.
    ``trip_headsigns`` aggregates distinct non-empty headsigns observed in
    ``static_trips`` for the route, so multi-direction keito numbers can
    expand into named direction options in the picker.
    """
    rows = await conn.fetch(
        "SELECT r.route_id, r.route_short_name, r.route_long_name, "
        "  regexp_replace(r.route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, "
        "  ARRAY(SELECT DISTINCT t.trip_headsign "
        "        FROM static_trips t "
        "        WHERE t.agency_id = r.agency_id "
        "          AND t.route_id = r.route_id "
        "          AND t.trip_headsign IS NOT NULL "
        "          AND t.trip_headsign <> '' "
        "        ORDER BY 1) AS trip_headsigns "
        "FROM static_routes r "
        "WHERE r.agency_id=$1 ORDER BY r.route_id",
        agency_id,
    )
    return [dict(r) for r in rows]


@router.get("/stops")
async def list_stops(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM static_stops WHERE agency_id=$1 ORDER BY stop_id",
        agency_id,
    )
    return [dict(r) for r in rows]
