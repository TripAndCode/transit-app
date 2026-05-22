import psycopg2

_DOW_PG = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 0}

# Take the most recent dep_delay estimate per (route, service, scheduled,
# trip, date, stop_seq). GTFS-RT feeds publish refining estimates as the
# trip nears each stop; the LAST observation before the stop event is
# what passengers actually experienced. MAX (the previous choice) biased
# toward whichever poll happened to catch the deepest delay spike.
#
# DISTINCT ON is the Postgres-idiomatic latest-per-group: the SELECT
# returns one row per (lead) group and ORDER BY picks the winner. The
# ORDER BY must lead with the DISTINCT ON columns in the same order;
# adding captured_at DESC at the tail picks the latest. The existing
# (agency_id, captured_at) index supports this.
_DEDUP_INNER = """\
        SELECT DISTINCT ON (route_code, service_type, scheduled_time,
                            trip_id, captured_at::date, stop_sequence)
               route_code, service_type, scheduled_time, trip_id,
               captured_at::date AS date, stop_sequence, dep_delay
        FROM updates
        WHERE dep_delay IS NOT NULL AND agency_id = %(agency_id)s
        ORDER BY route_code, service_type, scheduled_time, trip_id,
                 captured_at::date, stop_sequence, captured_at DESC"""


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
