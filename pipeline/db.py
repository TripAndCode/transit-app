import psycopg2

# Day-of-week map: PostgreSQL EXTRACT(DOW) returns 0=Sunday ... 6=Saturday
_DOW_PG = {"月": "1", "火": "2", "水": "3", "木": "4", "金": "5", "土": "6", "日": "0"}

# Template — caller must supply agency_id as the first bind param ($1 / %s)
_DEDUP_INNER = """\
        SELECT route_code, service_type, scheduled_time,
               trip_id, DATE(captured_at) AS date, stop_sequence,
               MAX(dep_delay) AS dep_delay
        FROM updates WHERE dep_delay IS NOT NULL AND agency_id = %(agency_id)s
        GROUP BY route_code, service_type, trip_id, DATE(captured_at), stop_sequence"""

_DEDUP_TEMPLATE = """\
    WITH deduped AS (
{inner}
    )
"""


def _dedup_cte(agency_id: int) -> tuple[str, list]:
    """Return (CTE SQL, params) with agency_id bound."""
    return _DEDUP_TEMPLATE.format(inner=_DEDUP_INNER), [agency_id]


_VALID_PARTITIONS = frozenset({
    "route_code",
    "route_code, service_type",
    "route_code, service_type, scheduled_time",
    "route_code, service_type, stop_sequence",
})


def _pct_sql(partition: str, agency_id: int) -> tuple[str, list]:
    if partition not in _VALID_PARTITIONS:
        raise ValueError(f"Invalid partition: {partition!r}")
    sql = (
        f"WITH deduped AS ({_DEDUP_INNER}),\n"
        f"ranked AS (\n"
        f"    SELECT *, PERCENT_RANK() OVER "
        f"(PARTITION BY {partition} ORDER BY dep_delay) AS pct\n"
        f"    FROM deduped\n"
        f")\n"
    )
    return sql, [agency_id]


def _agg_loaded(conn, agency_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agg_route_stats WHERE agency_id = %s LIMIT 1",
                (agency_id,),
            )
            return cur.fetchone() is not None
    except psycopg2.errors.UndefinedTable:
        return False


def _static_loaded(conn, agency_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM static_stops WHERE agency_id = %s LIMIT 1",
                (agency_id,),
            )
            return cur.fetchone() is not None
    except psycopg2.errors.UndefinedTable:
        return False
