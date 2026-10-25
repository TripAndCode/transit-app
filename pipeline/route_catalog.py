"""The agency's current timetable as a set of route codes, and the observed
route codes it no longer runs.

A timetable change renumbers routes, so a window that spans one carries the
old codes for its first days. Ranked against routes observed every day, a
retired code with a bad week leads the list under a bare id no route name
can be resolved for. :func:`retired_route_codes` names those codes so a
route list can leave them out by default and say how many it left.
"""

from __future__ import annotations

from api.range import RangeCtx

# The route_code a static route's id maps to. Aomori's ids carry the code in a
# parenthesised tail ('国道・古川線(1021)'); every other feed's id is its code,
# which the replace leaves as it is.
ROUTE_CODE_SQL = "regexp_replace({route_id}, '.*\\((\\d+)\\)$', '\\1')"

# How recently, before the agency's last observed day in the window, a route
# must have been seen to count as still running.
RECENT_DAYS = 7


def route_code_sql(route_id_column: str) -> str:
    """The SQL expression for ``route_id_column``'s route_code."""
    return ROUTE_CODE_SQL.format(route_id=route_id_column)


async def retired_route_codes(conn, agency_id: int, ctx: RangeCtx) -> set[str]:
    """Route codes observed in ``ctx`` that the current timetable lacks and
    that went unobserved over the last :data:`RECENT_DAYS` of the agency's data.

    The recent days end on the agency's last observed day in the window, not on
    ``ctx.to_date``: an agency whose data stops early would otherwise have
    every route look retired. An agency with no timetable loaded retires
    nothing, since there is nothing to compare against.
    """
    rows = await conn.fetch(
        "WITH seen AS ("
        "  SELECT route_code, max(date) AS last_seen FROM agg_route_daily"
        "  WHERE agency_id = $1 AND date >= $2 AND date <= $3"
        "  GROUP BY route_code"
        "), timetable AS ("
        f"  SELECT DISTINCT {route_code_sql('route_id')} AS route_code"
        "  FROM static_routes WHERE agency_id = $1"
        ") "
        "SELECT s.route_code FROM seen s "
        "WHERE EXISTS (SELECT 1 FROM timetable) "
        "  AND s.route_code NOT IN (SELECT route_code FROM timetable) "
        "  AND s.last_seen <= (SELECT max(last_seen) FROM seen) - $4::int",
        agency_id,
        ctx.from_date,
        ctx.to_date,
        RECENT_DAYS,
    )
    return {r["route_code"] for r in rows}
