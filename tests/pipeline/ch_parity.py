"""Parity oracle: the Postgres builders the ClickHouse-computed aggregates replaced.

Each SQL below is the builder pipeline/analyze.py ran for that table before it
moved to ClickHouse, copied verbatim. Never edit one to make a parity test pass:
a parity test asks whether the ClickHouse build reproduces exactly this.
"""

import psycopg2.extras

from api.range import time_band_case_sql
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
    "captured_at timestamptz"
)

# _analyze_alltime's columns and slice, as analyze loaded them for the whole-history builders.
_ALLTIME_COLUMNS = (
    "route_code text, service_type text, scheduled_time time, trip_id text, date date, stop_sequence int, dep_delay int"
)
_ALLTIME_TABLES = frozenset({"agg_route_stats", "agg_route_hour", "agg_route_hour_dow"})
# agg_stop_routes' key scan: every routed `updates` row, delay or not, loaded into _analyze_raw_keys.
_RAW_KEYS_SQL = (
    "SELECT DISTINCT route_code, trip_id, stop_sequence FROM updates "
    "WHERE agency_id = {agency_id:UInt16} AND route_code IS NOT NULL"
)
_RAW_KEYS_COLUMNS = "route_code text, trip_id text, stop_sequence int"
# agg_stop_daily's and agg_route_stop_daily's builders read their band CASE through this name.
band_case = time_band_case_sql("d.scheduled_time")
# agg_route_stats's builder read its two thresholds through these names.
on_time_thr = LEGACY_ON_TIME_LATE_TOLERANCE_SEC
late_thr = LEGACY_SEVERE_LATE_TOLERANCE_SEC

_BUCKET_EXPR = (
    f"CASE WHEN dep_delay < {LO} THEN 0 "
    f"WHEN dep_delay >= {HI} THEN {N_BUCKETS - 1} "
    f"ELSE 1 + (dep_delay - ({LO})) / {WIDTH} END"
)
_HIST_ARRAY = "ARRAY[" + ", ".join(f"COUNT(*) FILTER (WHERE b = {i})" for i in range(N_BUCKETS)) + "]::int[]"

LEGACY_SQL = {
    "agg_stop_routes": """
                        SELECT %(agency_id)s, sst.stop_id,
                               string_agg(DISTINCT k.route_code, ',' ORDER BY k.route_code)
                        FROM _analyze_raw_keys k
                        JOIN static_stop_times sst
                          ON sst.agency_id = %(agency_id)s
                         AND sst.trip_id = k.trip_id
                         AND sst.stop_sequence = k.stop_sequence
                        GROUP BY sst.stop_id
                    """,
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
    "agg_route_stats": f"""
            WITH deduped AS (SELECT * FROM _analyze_alltime WHERE route_code IS NOT NULL),
            grouped AS (
                SELECT
                    route_code, service_type,
                    AVG(dep_delay) AS avg_delay_sec,
                    -- PERCENTILE_DISC (not _CONT) so p50_min/p90_min are always
                    -- an actual observed dep_delay value. It handles ties and
                    -- single-row groups correctly by construction: it picks the
                    -- smallest ordered value whose cumulative distribution is
                    -- >= the target fraction, so a tied cluster or an n=1
                    -- partition always resolves to a real value instead of
                    -- skipping every row below the threshold. NOTE: this does
                    -- NOT match pipeline/reports/rankings.py's _ranking_live
                    -- ClickHouse fallback, which intentionally uses a
                    -- min-rank PERCENT_RANK() tie formula — see
                    -- that function's docstring for the known, accepted
                    -- divergence on tied data. Computing both fractions from
                    -- one ARRAY[...] call (rather than two separate
                    -- PERCENTILE_DISC calls) keeps this to a single per-group
                    -- sort of dep_delay.
                    PERCENTILE_DISC(ARRAY[0.5, 0.9]) WITHIN GROUP (ORDER BY dep_delay) AS pctl_sec,
                    SUM(CASE WHEN dep_delay>{late_thr} THEN 1 ELSE 0 END) AS late_5min_plus,
                    SUM(CASE WHEN dep_delay<={on_time_thr} THEN 1.0 ELSE 0 END)*100.0/COUNT(*) AS on_time_pct_raw,
                    SUM(CASE WHEN dep_delay>{late_thr} THEN 1.0 ELSE 0 END)*100.0/COUNT(*) AS late5_pct_raw,
                    COUNT(*) AS samples,
                    SUM(dep_delay) AS sum_delay_sec
                FROM deduped
                GROUP BY route_code, service_type
            )
            SELECT
                %(agency_id)s AS agency_id,
                route_code, service_type,
                ROUND(avg_delay_sec/60.0::numeric, 2) AS avg_min,
                ROUND(pctl_sec[1]/60.0::numeric, 2) AS p50_min,
                ROUND(pctl_sec[2]/60.0::numeric, 2) AS p90_min,
                late_5min_plus,
                ROUND(on_time_pct_raw, 1) AS on_time_pct,
                ROUND(late5_pct_raw, 1) AS late5_pct,
                samples,
                sum_delay_sec
            FROM grouped
            ORDER BY avg_min DESC
        """,
    "agg_route_hour": """
            WITH deduped AS (
                SELECT * FROM _analyze_alltime WHERE route_code IS NOT NULL AND scheduled_time IS NOT NULL
            ),
            grouped AS (
                SELECT
                    route_code, service_type, scheduled_time,
                    AVG(dep_delay) AS avg_delay_sec,
                    -- PERCENTILE_DISC: see agg_route_stats's identical column above.
                    PERCENTILE_DISC(ARRAY[0.5, 0.9]) WITHIN GROUP (ORDER BY dep_delay) AS pctl_sec,
                    COUNT(*) AS samples,
                    SUM(dep_delay) AS sum_delay_sec
                FROM deduped
                GROUP BY route_code, service_type, scheduled_time
            )
            SELECT
                %(agency_id)s AS agency_id,
                route_code, service_type, scheduled_time,
                ROUND(avg_delay_sec/60.0::numeric, 2) AS avg_min,
                ROUND(pctl_sec[1]/60.0::numeric, 2) AS p50_min,
                ROUND(pctl_sec[2]/60.0::numeric, 2) AS p90_min,
                samples,
                sum_delay_sec
            FROM grouped
            ORDER BY route_code, scheduled_time
        """,
    "agg_stop_daily": f"""
                WITH deduped AS (SELECT * FROM _analyze_deduped)
                SELECT
                    %(agency_id)s, sst.stop_id, d.date, COALESCE(d.service_type, ''),
                    {band_case} AS time_band,
                    SUM(d.dep_delay)::bigint, COUNT(*)
                FROM deduped d
                JOIN static_stop_times sst
                  ON sst.agency_id = %(agency_id)s
                 AND sst.trip_id = d.trip_id
                 AND sst.stop_sequence = d.stop_sequence
                GROUP BY sst.stop_id, d.date, COALESCE(d.service_type, ''), {band_case}
            """,
    "agg_route_stop_daily": f"""
                WITH deduped AS (SELECT * FROM _analyze_deduped WHERE route_code IS NOT NULL)
                SELECT
                    %(agency_id)s, d.route_code, sst.stop_id, d.date,
                    COALESCE(d.service_type, '') AS service_type,
                    {band_case} AS time_band,
                    SUM(d.dep_delay)::bigint, COUNT(*)
                FROM deduped d
                JOIN static_stop_times sst
                  ON sst.agency_id = %(agency_id)s
                 AND sst.trip_id = d.trip_id
                 AND sst.stop_sequence = d.stop_sequence
                GROUP BY d.route_code, sst.stop_id, d.date,
                         COALESCE(d.service_type, ''), {band_case}
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
    if table == "agg_stop_routes":
        load = {"table": "_analyze_raw_keys", "columns": _RAW_KEYS_COLUMNS, "ch_sql": _RAW_KEYS_SQL}
    elif table in _ALLTIME_TABLES:
        load = {
            "table": "_analyze_alltime",
            "columns": _ALLTIME_COLUMNS,
            "ch_sql": build_dedup_ch_sql(extra_where="u.service_type IS NOT NULL"),
        }
    else:
        load = {
            "table": "_analyze_deduped",
            "columns": _DEDUPED_COLUMNS,
            "ch_sql": build_dedup_ch_sql(include_captured_at=True),
        }
    with conn.cursor() as cur:
        _load_temp_table(cur, ch_client, **load, parameters={"agency_id": agency_id}, label="parity")
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
