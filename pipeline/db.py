import psycopg2

_DOW_PG = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 0}

_DEDUP_INNER = """\
        SELECT route_code, service_type, scheduled_time,
               trip_id, DATE(captured_at) AS date, stop_sequence,
               MAX(dep_delay) AS dep_delay
        FROM updates WHERE dep_delay IS NOT NULL AND agency_id = %(agency_id)s
        GROUP BY route_code, service_type, scheduled_time, trip_id, DATE(captured_at), stop_sequence"""


def _static_loaded(conn, agency_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM static_stops WHERE agency_id = %s LIMIT 1",
                (agency_id,),
            )
            return cur.fetchone() is not None
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        return False
