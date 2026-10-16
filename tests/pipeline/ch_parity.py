"""Parity oracle: the Postgres builders the ClickHouse-computed aggregates replaced.

Each SQL below is the builder pipeline/analyze.py ran for that table before it
moved to ClickHouse, copied verbatim. Never edit one to make a parity test pass:
a parity test asks whether the ClickHouse build reproduces exactly this.
"""

import psycopg2.extras

from pipeline import ch_aggregates
from pipeline.analyze import _load_temp_table
from pipeline.db import build_dedup_ch_sql

# _analyze_deduped's columns in build_dedup_ch_sql's SELECT order, as analyze loaded them.
_DEDUPED_COLUMNS = (
    "route_code text, service_type text, scheduled_time time, "
    "trip_id text, date date, stop_sequence int, dep_delay int, "
    "captured_at timestamptz, arr_delay int"
)

LEGACY_SQL = {
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
    with conn.cursor() as cur:
        _load_temp_table(
            cur,
            ch_client,
            table="_analyze_deduped",
            columns=_DEDUPED_COLUMNS,
            ch_sql=build_dedup_ch_sql(include_captured_at=True, include_arr_delay=True),
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
