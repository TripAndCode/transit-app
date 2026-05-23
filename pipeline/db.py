"""Shared DB helpers and SQL builders used by both psycopg2 (analyze) and
asyncpg (api / reports / executor) paths.

The dedup SQL lives here so the two paths never drift apart. Update one
place and every report endpoint, the analyze materializer, and the legacy
/query route all pick up the change atomically.
"""

import psycopg2

# ISODOW (Mon=1..Sun=7). Aligns with api/range.dow_clause and agg_route_dow.dow
# (SMALLINT) after migration 0011.
_DOW_JP_TO_ISO = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 7}
_DOW_ISO_TO_JP = {v: k for k, v in _DOW_JP_TO_ISO.items()}


def build_dedup_inner_sql(
    *,
    placeholder: str = "%(agency_id)s",
    extra_where: str = "",
) -> str:
    """Return the SQL body that picks the latest observation per stop event.

    GTFS-RT feeds publish refining `dep_delay` estimates as the trip nears
    each stop. The latest observation is what passengers actually
    experienced; `MAX(dep_delay)` (the previous behavior) biased toward
    the deepest mid-flight overestimate.

    Rows with `dep_delay IS NULL` are filtered at the inner SELECT so the
    function surfaces the last NUMERIC estimate per group, not a trailing
    "no estimate" row.

    `placeholder` selects the parameter style for `agency_id`:
    `%(agency_id)s` for psycopg2 (analyze.py), `$1` for asyncpg
    (executor.py, reports.py). `extra_where` is ANDed onto the inner
    WHERE; pass server-built SQL only.

    The trailing `id DESC` makes the dedup deterministic when two rows
    share the same `captured_at` (different files, same poll second).
    """
    # Wrap in parens so a fragment containing top-level OR composes correctly.
    extra = f" AND ({extra_where})" if extra_where else ""
    return (
        "SELECT DISTINCT ON (route_code, service_type, scheduled_time, "
        "trip_id, captured_at::date, stop_sequence) "
        "route_code, service_type, scheduled_time, trip_id, "
        "captured_at::date AS date, stop_sequence, dep_delay "
        "FROM updates "
        f"WHERE dep_delay IS NOT NULL AND agency_id = {placeholder}{extra} "
        "ORDER BY route_code, service_type, scheduled_time, trip_id, "
        "captured_at::date, stop_sequence, captured_at DESC, id DESC"
    )


# Psycopg2 binding used by pipeline/analyze.py.
_DEDUP_INNER = build_dedup_inner_sql()


def _static_loaded(conn, agency_id: int) -> bool:
    """Return True iff the agency has any rows in `static_stops`."""
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
