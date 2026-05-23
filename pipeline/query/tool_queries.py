"""SQL helpers backing the LLM tool surface (pipeline/query/tools.py).

Each function is small, focused, and returns plain rows / tuples that
tools.py wraps in a ToolResult. None of these are exposed via REST —
the legacy /api/{id}/query route was retired with executor.py
(2026-05-23). SQL is lifted from the corresponding _exec_* functions
to preserve LLM-answer behaviour byte-for-byte.
"""

from api.range import RangeCtx, build_updates_filter
from pipeline.db import build_dedup_inner_sql


async def route_dow_breakdown(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    *,
    route: str,
) -> list[tuple]:
    """Per-DOW delay summary for one route over ctx.

    Returns rows: (route_code, service_type, dow_iso, avg_min, samples)
    sorted by avg_min DESC. dow_iso is ISODOW (Mon=1..Sun=7) per
    migration 0011. Backs tools._tool_route_stats.
    """
    where_frag, params, _ = build_updates_filter(ctx, next_param=3)
    sql = (
        f"WITH deduped AS ({build_dedup_inner_sql(placeholder='$1', extra_where=where_frag)}) "
        "SELECT route_code, service_type, "
        "       EXTRACT(ISODOW FROM date::date)::smallint AS dow, "
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min, "
        "       COUNT(*) AS samples "
        "FROM deduped WHERE route_code = $2 "
        "GROUP BY route_code, service_type, EXTRACT(ISODOW FROM date::date) "
        "ORDER BY avg_min DESC NULLS LAST"
    )
    rows = await conn.fetch(sql, agency_id, str(route), *params)
    return [tuple(r) for r in rows]


async def route_compare_service(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    *,
    route: str,
) -> list[tuple]:
    """Single-route weekday vs weekend delay comparison over ctx.

    Returns rows: (service_type, avg_min, samples) — typically two rows
    ('平日' and '土日祝'). Backs tools._tool_compare_segments
    (dimension='service_type').
    """
    where_frag, params, _ = build_updates_filter(ctx, next_param=3)
    sql = (
        f"WITH deduped AS ({build_dedup_inner_sql(placeholder='$1', extra_where=where_frag)}) "
        "SELECT service_type, "
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min, "
        "       COUNT(*) AS samples "
        "FROM deduped WHERE route_code = $2 "
        "GROUP BY service_type"
    )
    rows = await conn.fetch(sql, agency_id, str(route), *params)
    return [tuple(r) for r in rows]


async def route_info(agency_id: int, conn, *, route: str) -> tuple | None:
    """Static route metadata: route_id, route_short_name, stop count,
    first/last departure, trip count. Returns None when the route isn't
    in the agency's static GTFS at all. Backs tools._tool_route_meta.
    """
    row = await conn.fetchrow(
        "SELECT sr.route_id, sr.route_short_name, "
        "       COUNT(DISTINCT sst.stop_id) AS stop_count, "
        "       MIN(sst.departure_time) AS first_dep, "
        "       MAX(sst.departure_time) AS last_dep, "
        "       COUNT(DISTINCT st.trip_id) AS trip_count "
        "FROM static_routes sr "
        "JOIN static_trips st ON st.route_id = sr.route_id AND st.agency_id=$1 "
        "JOIN static_stop_times sst ON sst.trip_id = st.trip_id AND sst.agency_id=$1 "
        "WHERE sr.agency_id=$1 AND sr.route_id LIKE '%(' || $2 || ')' "
        "GROUP BY sr.route_id, sr.route_short_name",
        agency_id,
        str(route),
    )
    return tuple(row) if row else None
