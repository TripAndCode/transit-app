from fastapi import APIRouter, Depends

from api.deps import get_conn, get_agency

router = APIRouter(prefix="/api/{agency_id}", tags=["static"])


@router.get("/routes")
async def list_routes(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT route_id, route_short_name FROM static_routes "
        "WHERE agency_id=$1 ORDER BY route_id",
        agency_id,
    )
    return [dict(r) for r in rows]


@router.get("/stops")
async def list_stops(
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT stop_id, stop_name, stop_lat, stop_lon "
        "FROM static_stops WHERE agency_id=$1 ORDER BY stop_id",
        agency_id,
    )
    return [dict(r) for r in rows]
