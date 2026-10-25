import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import limiter, tier_limit
from pipeline.route_catalog import route_code_sql

router = APIRouter(prefix="/api/{agency_id}", tags=["static"])


class StaticRoute(BaseModel):
    route_id: str
    route_short_name: str | None
    route_long_name: str | None
    route_code: str | None
    trip_headsigns: list[str]
    origin_name: str | None
    via_name: str | None
    destination_name: str | None
    is_loop: bool
    bidirectional: bool


class StaticStop(BaseModel):
    stop_id: str
    stop_name: str | None
    stop_lat: float | None
    stop_lon: float | None


class StaticRoutesResponse(BaseModel):
    """Envelope for ``GET /routes`` — an object, so the catalogue can grow a
    total or a paging cursor without breaking every client at once."""

    rows: list[StaticRoute]


class StaticStopsResponse(BaseModel):
    """Envelope for ``GET /stops`` — see :class:`StaticRoutesResponse`."""

    rows: list[StaticStop]


@router.get("/routes", response_model=StaticRoutesResponse)
@limiter.limit(tier_limit)
async def list_routes(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
) -> StaticRoutesResponse:
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
    ``origin_name``/``via_name``/``destination_name`` are the stop names the
    route runs from, through and to, each null when the feed gives no way to
    name it. ``is_loop`` means the origin and destination are the same stop
    name; ``bidirectional`` means the route's trips also run the way back. At
    most one of the two is true. All five are computed when the static feed
    loads (see ``pipeline.route_terminals``).
    """
    rows = await conn.fetch(
        "SELECT r.route_id, r.route_short_name, r.route_long_name, "
        f"  {route_code_sql('r.route_id')} AS route_code, "
        "  ARRAY(SELECT DISTINCT t.trip_headsign "
        "        FROM static_trips t "
        "        WHERE t.agency_id = r.agency_id "
        "          AND t.route_id = r.route_id "
        "          AND t.trip_headsign IS NOT NULL "
        "          AND t.trip_headsign <> '' "
        "        ORDER BY 1) AS trip_headsigns, "
        "  r.origin_name, r.via_name, r.destination_name, r.is_loop, r.bidirectional "
        "FROM static_routes r "
        "WHERE r.agency_id=$1 ORDER BY r.route_id",
        agency_id,
    )
    return StaticRoutesResponse(rows=[StaticRoute(**dict(r)) for r in rows])


@router.get("/stops", response_model=StaticStopsResponse)
@limiter.limit(tier_limit)
async def list_stops(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
) -> StaticStopsResponse:
    rows = await conn.fetch(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM static_stops WHERE agency_id=$1 ORDER BY stop_id",
        agency_id,
    )
    return StaticStopsResponse(rows=[StaticStop(**dict(r)) for r in rows])
