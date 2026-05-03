from fastapi import APIRouter, Depends

from api.deps import get_agency, get_conn

router = APIRouter(prefix="/api/{agency_id}", tags=["static"])


@router.get("/routes")
async def list_routes(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """List of static routes plus the numeric ``route_code`` used by updates.

    The trip-id-derived ``route_code`` (e.g. '1021') is extracted from the
    parenthesised tail of ``route_id`` (e.g. '国道・古川線(1021)') so the
    frontend can decorate ranking rows with the human-readable
    ``route_short_name``.
    """
    rows = await conn.fetch(
        "SELECT route_id, route_short_name, "
        "  regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code "
        "FROM static_routes WHERE agency_id=$1 ORDER BY route_id",
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
