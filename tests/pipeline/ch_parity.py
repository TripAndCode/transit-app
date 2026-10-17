"""Parity oracle: the Postgres builders the ClickHouse-computed aggregates replaced.

Each SQL below is the builder pipeline/analyze.py ran for that table before it
moved to ClickHouse, copied verbatim. Never edit one to make a parity test pass:
a parity test asks whether the ClickHouse build reproduces exactly this.
"""

import psycopg2.extras

from pipeline import ch_aggregates
from pipeline.analyze import _load_temp_table
from pipeline.db import build_dedup_ch_sql
from pipeline.histogram import (
    HI,
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    LO,
    N_BUCKETS,
    WIDTH,
)

# _analyze_deduped's columns in build_dedup_ch_sql's SELECT order, as analyze loaded them.
_DEDUPED_COLUMNS = (
    "route_code text, service_type text, scheduled_time time, "
    "trip_id text, date date, stop_sequence int, dep_delay int, "
    "captured_at timestamptz, arr_delay int"
)

# _analyze_alltime's columns and slice, as analyze loaded them for the whole-history builders.
_ALLTIME_COLUMNS = (
    "route_code text, service_type text, scheduled_time time, trip_id text, date date, stop_sequence int, dep_delay int"
)
_ALLTIME_TABLES = frozenset({"agg_route_hour_dow"})

_BUCKET_EXPR = (
    f"CASE WHEN dep_delay < {LO} THEN 0 "
    f"WHEN dep_delay >= {HI} THEN {N_BUCKETS - 1} "
    f"ELSE 1 + (dep_delay - ({LO})) / {WIDTH} END"
)
_HIST_ARRAY = "ARRAY[" + ", ".join(f"COUNT(*) FILTER (WHERE b = {i})" for i in range(N_BUCKETS)) + "]::int[]"

LEGACY_SQL = {
    "agg_route_daily_dist": f"""
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE route_code IS NOT NULL),
            bucketed AS (
                SELECT date, route_code,
                    COALESCE(service_type, '') AS service_type,
                    dep_delay, {_BUCKET_EXPR} AS b
                FROM deduped
            )
            SELECT
                %(agency_id)s AS agency_id,
                date::text, route_code, service_type,
                COUNT(*)                              AS samples,
                SUM(dep_delay)                        AS sum_delay_sec,
                COUNT(*) FILTER (WHERE dep_delay <= {LEGACY_ON_TIME_LATE_TOLERANCE_SEC})  AS on_time_count,
                COUNT(*) FILTER (WHERE dep_delay > {LEGACY_SEVERE_LATE_TOLERANCE_SEC})  AS late5_count,
                {_HIST_ARRAY}                         AS hist
            FROM bucketed
            GROUP BY date, route_code, service_type
            ORDER BY date, route_code
        """,
    "agg_daily_trend": """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE route_code IS NOT NULL)
            SELECT
                %(agency_id)s AS agency_id,
                date, route_code,
                COALESCE(service_type, '') AS service_type,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec,
                SUM(GREATEST(dep_delay, 0)) AS sum_late_sec
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """,
    "agg_route_daily": """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE route_code IS NOT NULL)
            SELECT
                %(agency_id)s AS agency_id,
                date::text, route_code,
                COALESCE(service_type, '') AS service_type,
                ROUND(AVG(dep_delay))::int AS avg_delay_sec,
                MAX(dep_delay)             AS worst_delay_sec,
                COUNT(DISTINCT trip_id)    AS trips_observed,
                COUNT(*)                   AS samples,
                MAX(captured_at)           AS last_seen_at,
                SUM(dep_delay)             AS sum_delay_sec
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """,
    "agg_hour_daily": """
            WITH deduped AS (SELECT * FROM _analyze_deduped)
            SELECT
                %(agency_id)s AS agency_id,
                date,
                EXTRACT(HOUR FROM scheduled_time)::smallint AS hour,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec
            FROM deduped
            WHERE scheduled_time IS NOT NULL
            GROUP BY date, EXTRACT(HOUR FROM scheduled_time)
            ORDER BY date, hour
        """,
    "agg_route_hour_dow": """
            WITH deduped AS (
                SELECT * FROM _analyze_alltime WHERE route_code IS NOT NULL AND scheduled_time IS NOT NULL
            )
            SELECT
                %(agency_id)s AS agency_id,
                route_code, service_type,
                EXTRACT(ISODOW FROM date::date)::smallint AS dow,
                EXTRACT(HOUR FROM scheduled_time)::smallint AS hour,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec
            FROM deduped
            GROUP BY route_code, service_type,
                     EXTRACT(ISODOW FROM date::date), EXTRACT(HOUR FROM scheduled_time)
            ORDER BY route_code
        """,
}


def legacy_rows(conn, ch_client, agency_id: int, table: str) -> list[str]:
    """The rows the legacy builder would store for *table*, as Postgres holds them.

    Built from the same ClickHouse dedup analyze loaded, then inserted the way
    the legacy builder inserted them (execute_batch into the table's own column
    types) and read back, so a float4 or a numeric's scale comes out as stored.
    Sorted and rendered with str(), so the scale counts.
    """
    columns = ", ".join(ch_aggregates.COLUMNS[table])
    placeholders = ", ".join(["%s"] * len(ch_aggregates.COLUMNS[table]))
    alltime = table in _ALLTIME_TABLES
    with conn.cursor() as cur:
        _load_temp_table(
            cur,
            ch_client,
            table="_analyze_alltime" if alltime else "_analyze_deduped",
            columns=_ALLTIME_COLUMNS if alltime else _DEDUPED_COLUMNS,
            ch_sql=(
                build_dedup_ch_sql(extra_where="u.service_type IS NOT NULL")
                if alltime
                else build_dedup_ch_sql(include_captured_at=True, include_arr_delay=True)
            ),
            parameters={"agency_id": agency_id},
            label="parity",
        )
        cur.execute(LEGACY_SQL[table], {"agency_id": agency_id})
        rows = cur.fetchall()
        cur.execute(f"CREATE TEMP TABLE _parity_{table} (LIKE {table}) ON COMMIT DROP")
        psycopg2.extras.execute_batch(cur, f"INSERT INTO _parity_{table} ({columns}) VALUES ({placeholders})", rows)
        cur.execute(f"SELECT {columns} FROM _parity_{table}")
        stored = cur.fetchall()
    conn.rollback()
    return sorted(map(str, stored))


def stored_rows(conn, agency_id: int, table: str) -> list[str]:
    """The agency's rows of *table* in Postgres, in legacy_rows's form."""
    columns = ", ".join(ch_aggregates.COLUMNS[table])
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns} FROM {table} WHERE agency_id = %s", (agency_id,))
        return sorted(map(str, cur.fetchall()))
