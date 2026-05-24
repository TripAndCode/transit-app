"""Materialise per-agency aggregation tables from the `updates` fact table.

Called by `gtfs_pipeline.py analyze` after ingestion. All tables are written
via INSERT … ON CONFLICT DO UPDATE so the function is safe to re-run.

Aggregation tables produced:
- agg_route_stats   — overall delay stats per route/service_type
- agg_route_hour    — delay by scheduled departure time
- agg_route_dow     — delay by day-of-week (ISODOW 1=Mon..7=Sun)
- agg_daily_trend   — per-day delay averages for trend queries
- agg_stop_seq      — per-stop delay (requires static data)
"""

import psycopg2.extras

from pipeline.db import _DEDUP_INNER, _static_loaded

# Order matters only for log/diff determinism; FK independence means
# DELETE order has no semantic effect.
_AGG_TABLES_ORDERED = (
    "agg_route_stats",
    "agg_route_hour",
    "agg_route_dow",
    "agg_daily_trend",
    "agg_stop_seq",
)
_VALID_AGG_TABLES = frozenset(_AGG_TABLES_ORDERED)


def _run_query(sql: str, params: dict, conn) -> list:
    """Execute *sql* with *params* via psycopg2 and return all rows."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _upsert_agg(table: str, pk_cols: list, col_names: list, rows: list, conn) -> None:
    """Bulk-upsert *rows* into *table* using INSERT … ON CONFLICT DO UPDATE.

    Args:
        table:     Target aggregation table (must be in _VALID_AGG_TABLES).
        pk_cols:   Column names that form the conflict target.
        col_names: All column names in insertion order (must match *rows* tuple width).
        rows:      Iterable of tuples to insert.
        conn:      psycopg2 connection; committed on success.
    """
    if table not in _VALID_AGG_TABLES:
        raise ValueError(f"Unknown aggregation table: {table!r}")
    if not rows:
        return
    col_list = ", ".join(col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    conflict_cols = ", ".join(pk_cols)
    update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in col_names if c not in pk_cols)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_set}"
    )
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows)
    conn.commit()


def _insert_agg(table: str, col_names: list, rows: list, conn) -> None:
    """Bulk-INSERT *rows* into *table* in the current transaction.

    Caller guarantees the table is empty for the agency_id being
    materialised. No commit — `analyze` controls the transaction so the
    DELETE + 5 INSERTs land atomically.
    """
    if table not in _VALID_AGG_TABLES:
        raise ValueError(f"Unknown aggregation table: {table!r}")
    if not rows:
        return
    col_list = ", ".join(col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows)


def analyze(agency_id: int, conn) -> None:
    """Compute and materialise all aggregation tables for *agency_id*.

    Reads from `updates` (deduplicated via _DEDUP_INNER CTE) and writes to
    agg_route_stats, agg_route_hour, agg_route_dow, agg_daily_trend, and
    agg_stop_seq. Each table is upserted so re-runs are safe.
    """
    p = {"agency_id": agency_id}

    # ── agg_route_stats ──────────────────────────────────────────────────────
    sql = f"""
        WITH deduped AS ({_DEDUP_INNER}),
        ranked AS (
            SELECT *, PERCENT_RANK() OVER (
                PARTITION BY route_code, service_type ORDER BY dep_delay
            ) AS pct FROM deduped
        )
        SELECT
            %(agency_id)s AS agency_id,
            route_code, service_type,
            ROUND(AVG(dep_delay)/60.0::numeric, 2)  AS avg_min,
            ROUND(MIN(CASE WHEN pct>=0.5 THEN dep_delay END)/60.0::numeric, 2) AS p50_min,
            ROUND(MIN(CASE WHEN pct>=0.9 THEN dep_delay END)/60.0::numeric, 2) AS p90_min,
            SUM(CASE WHEN dep_delay>300 THEN 1 ELSE 0 END)  AS late_5min_plus,
            ROUND(SUM(CASE WHEN dep_delay<=60 THEN 1.0 ELSE 0 END)*100.0/COUNT(*), 1) AS on_time_pct,
            ROUND(SUM(CASE WHEN dep_delay>300 THEN 1.0 ELSE 0 END)*100.0/COUNT(*), 1) AS late5_pct,
            COUNT(*) AS samples
        FROM ranked
        GROUP BY route_code, service_type
        HAVING COUNT(*) > 20
        ORDER BY avg_min DESC
    """
    rows = _run_query(sql, p, conn)
    _upsert_agg(
        "agg_route_stats",
        ["agency_id", "route_code", "service_type"],
        [
            "agency_id",
            "route_code",
            "service_type",
            "avg_min",
            "p50_min",
            "p90_min",
            "late_5min_plus",
            "on_time_pct",
            "late5_pct",
            "samples",
        ],
        rows,
        conn,
    )
    print(f"  agg_route_stats: {len(rows)} rows")

    # ── agg_route_hour ───────────────────────────────────────────────────────
    sql = f"""
        WITH deduped AS ({_DEDUP_INNER}),
        ranked AS (
            SELECT *, PERCENT_RANK() OVER (
                PARTITION BY route_code, service_type, scheduled_time ORDER BY dep_delay
            ) AS pct FROM deduped
        )
        SELECT
            %(agency_id)s AS agency_id,
            route_code, service_type, scheduled_time,
            ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
            ROUND(MIN(CASE WHEN pct>=0.5 THEN dep_delay END)/60.0::numeric, 2) AS p50_min,
            ROUND(MIN(CASE WHEN pct>=0.9 THEN dep_delay END)/60.0::numeric, 2) AS p90_min,
            COUNT(*) AS samples
        FROM ranked
        GROUP BY route_code, service_type, scheduled_time
        ORDER BY route_code, scheduled_time
    """
    rows = _run_query(sql, p, conn)
    _upsert_agg(
        "agg_route_hour",
        ["agency_id", "route_code", "service_type", "scheduled_time"],
        ["agency_id", "route_code", "service_type", "scheduled_time", "avg_min", "p50_min", "p90_min", "samples"],
        rows,
        conn,
    )
    print(f"  agg_route_hour: {len(rows)} rows")

    # ── agg_route_dow ────────────────────────────────────────────────────────
    sql = f"""
        WITH deduped AS ({_DEDUP_INNER})
        SELECT
            %(agency_id)s AS agency_id,
            route_code, service_type,
            EXTRACT(ISODOW FROM date::date)::smallint AS dow,
            ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
            COUNT(*) AS samples
        FROM deduped
        GROUP BY route_code, service_type, EXTRACT(ISODOW FROM date::date)
        ORDER BY route_code
    """
    rows = _run_query(sql, p, conn)
    _upsert_agg(
        "agg_route_dow",
        ["agency_id", "route_code", "service_type", "dow"],
        ["agency_id", "route_code", "service_type", "dow", "avg_min", "samples"],
        rows,
        conn,
    )
    print(f"  agg_route_dow: {len(rows)} rows")

    # ── agg_daily_trend ──────────────────────────────────────────────────────
    sql = f"""
        WITH deduped AS ({_DEDUP_INNER})
        SELECT
            %(agency_id)s AS agency_id,
            date::text, route_code, service_type,
            ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
            COUNT(*) AS samples
        FROM deduped
        GROUP BY date, route_code, service_type
        ORDER BY date, route_code
    """
    rows = _run_query(sql, p, conn)
    _upsert_agg(
        "agg_daily_trend",
        ["agency_id", "date", "route_code", "service_type"],
        ["agency_id", "date", "route_code", "service_type", "avg_min", "samples"],
        rows,
        conn,
    )
    print(f"  agg_daily_trend: {len(rows)} rows")

    # ── agg_stop_seq ─────────────────────────────────────────────────────────
    if _static_loaded(conn, agency_id):
        sql = f"""
            WITH deduped AS ({_DEDUP_INNER})
            SELECT
                %(agency_id)s AS agency_id,
                d.route_code, d.stop_sequence,
                COALESCE(MAX(ss.stop_name),
                         CAST(d.stop_sequence AS TEXT) || '番停留所') AS stop_name,
                ROUND(AVG(d.dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples
            FROM deduped d
            LEFT JOIN static_stop_times sst
                ON d.trip_id = sst.trip_id
                AND d.stop_sequence = sst.stop_sequence
                AND sst.agency_id = %(agency_id)s
            LEFT JOIN static_stops ss
                ON sst.stop_id = ss.stop_id
                AND ss.agency_id = %(agency_id)s
            WHERE d.stop_sequence IS NOT NULL
            GROUP BY d.route_code, d.stop_sequence
            HAVING COUNT(*) > 5
            ORDER BY ROUND(AVG(d.dep_delay)/60.0::numeric, 2) DESC
        """
    else:
        sql = f"""
            WITH deduped AS ({_DEDUP_INNER})
            SELECT
                %(agency_id)s AS agency_id,
                route_code, stop_sequence,
                CAST(stop_sequence AS TEXT) || '番停留所' AS stop_name,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples
            FROM deduped
            WHERE stop_sequence IS NOT NULL
            GROUP BY route_code, stop_sequence
            HAVING COUNT(*) > 5
            ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) DESC
        """
    rows = _run_query(sql, p, conn)
    _upsert_agg(
        "agg_stop_seq",
        ["agency_id", "route_code", "stop_sequence"],
        ["agency_id", "route_code", "stop_sequence", "stop_name", "avg_min", "samples"],
        rows,
        conn,
    )
    print(f"  agg_stop_seq: {len(rows)} rows")
    print("Analysis complete.")
