"""Materialise per-agency aggregation tables from the `updates` fact table.

Called by `gtfs_pipeline.py analyze` after ingestion. Each run wipes the
agency's agg_* tables and rewrites them from freshly computed
SELECTs in one transaction, so re-running is idempotent and a crash
mid-run rolls back to the prior snapshot.

Aggregation tables produced:
- agg_route_stats      — overall delay stats per route/service_type
- agg_route_hour       — delay by scheduled departure time
- agg_route_dow        — delay by day-of-week (ISODOW 1=Mon..7=Sun)
- agg_route_hour_dow   — delay by day-of-week × scheduled hour (Forecast heatmap)
- agg_daily_trend      — per-day delay averages for trend queries
- agg_route_daily      — per-route, per-day summary (powers today/route-summary)
- agg_route_daily_dist — per-day delay distribution (powers range-scoped reports)
- agg_hour_daily       — per-day, per-hour-of-day delay (Overview peak-hour-by-DOW)
- agg_stop_seq         — per-stop delay by sequence (synthesizes stop names without static)
- agg_stop_daily       — per-stop, per-day delay (powers the heatmap)
- agg_stop_routes      — routes serving each stop (heatmap labels)
- agg_route_stop_daily — per-route-per-stop, per-day delay (route-filtered heatmap)
- agg_feed_health      — per-day raw vs implausible-delay counts (data-quality signal)
- agg_meta             — audit row: last analyze() time per agency (forensic-only, not load-bearing)
"""

import logging
from datetime import timezone

import psycopg2.extras

from api.range import time_band_case_sql
from pipeline.clickhouse import max_captured_at as ch_max_captured_at
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, _static_loaded, build_dedup_ch_sql
from pipeline.histogram import HI, LO, N_BUCKETS, WIDTH

logger = logging.getLogger(__name__)

# SQL that bins dep_delay exactly like histogram.bucketize() — kept in lockstep
# with the read path by deriving both from the same LO/HI/WIDTH constants.
# Operands are non-negative inside the inner range, so SQL integer division
# matches Python floor division.
_BUCKET_EXPR = (
    f"CASE WHEN dep_delay < {LO} THEN 0 "
    f"WHEN dep_delay >= {HI} THEN {N_BUCKETS - 1} "
    f"ELSE 1 + (dep_delay - ({LO})) / {WIDTH} END"
)
# Fixed-length bucket-count array (one COUNT FILTER per bucket) — guarantees
# every row stores exactly N_BUCKETS counts regardless of which bins are empty.
_HIST_ARRAY = "ARRAY[" + ", ".join(f"COUNT(*) FILTER (WHERE b = {i})" for i in range(N_BUCKETS)) + "]::int[]"

# The deduped fact slice is materialised ONCE per agency into a TEMP TABLE (see
# analyze()), because the dedup is the expensive full-partition scan+sort and
# every aggregate needs it. It is the SUPERSET every builder reads from:
# UNTYPED (keeps NULL-service rows — notably agency 9, 広島バス — which the
# reports + route-summary surface), plus raw captured_at (agg_route_daily needs a
# per-day last_seen_at). The service_type-keyed aggregates (route_stats/hour/dow)
# filter `service_type IS NOT NULL` over the materialised set — equivalent to a
# typed dedup because service_type is part of the dedup key. NULL service is
# coalesced to '' where it must fit a NOT NULL column; the endpoints map it back.
#
# As of the ClickHouse migration, the dedup itself runs in ClickHouse
# (build_dedup_ch_sql, the `updates` fact table's new home) and the result is
# bulk-loaded into this same-shaped Postgres TEMP TABLE — every builder below
# is untouched, since it only ever read the materialised temp table, never
# `updates` directly.

# Order matters only for log/diff determinism; FK independence means
# DELETE order has no semantic effect.
_AGG_TABLES_ORDERED = (
    "agg_route_stats",
    "agg_route_hour",
    "agg_route_dow",
    "agg_route_hour_dow",
    "agg_daily_trend",
    "agg_route_daily",
    "agg_route_daily_dist",
    "agg_hour_daily",
    "agg_stop_seq",
    "agg_stop_daily",
    "agg_stop_routes",
    "agg_route_stop_daily",
    "agg_feed_health",
)
_VALID_AGG_TABLES = frozenset(_AGG_TABLES_ORDERED)


def _run_query(sql: str, params: dict, conn) -> list:
    """Execute *sql* with *params* via psycopg2 and return all rows."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _insert_agg(table: str, col_names: list, rows: list, conn) -> None:
    """Bulk-INSERT *rows* into *table* in the current transaction.

    Caller guarantees the table is empty for the agency_id being
    materialised. No commit — `analyze` controls the transaction so the
    DELETE + INSERTs land atomically.
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


def analyze(agency_id: int, conn, ch_client) -> None:
    """Compute and materialise all aggregation tables for *agency_id*.

    Wipes this agency's agg_* rows, then INSERTs the freshly
    computed set, all in one transaction. A crash mid-run rolls back to
    the prior snapshot so the agency is never observed empty. Re-running
    is idempotent — same inputs produce the same final state.

    *ch_client* is the ClickHouse client used to fetch the deduped fact
    slice (the `updates` fact table now lives in ClickHouse); every
    aggregate builder below still reads the Postgres TEMP TABLE it's
    loaded into, unchanged.
    """
    p = {"agency_id": agency_id, "max_delay": MAX_PLAUSIBLE_DELAY_SEC}
    # Resolved BEFORE the txn opens: _static_loaded calls conn.rollback() in
    # its UndefinedTable branch, which would silently wipe our DELETE + partial
    # INSERTs if it fired mid-run. Hoisting the probe keeps analyze's
    # transactional shape clean.
    has_static = _static_loaded(conn, agency_id)
    try:
        # ── Purge stale rows for this agency ─────────────────────────────
        with conn.cursor() as cur:
            for tbl in _AGG_TABLES_ORDERED:
                cur.execute(f"DELETE FROM {tbl} WHERE agency_id = %s", (agency_id,))

        # ── Materialise the deduped fact slice ONCE ──────────────────────
        # The dedup is a full-partition scan + sort; previously every
        # aggregate re-derived it (9 scans/agency) against Postgres. `updates`
        # now lives in ClickHouse, so the dedup runs there instead and the
        # result is bulk-loaded into the same-shaped Postgres TEMP TABLE every
        # builder below reads from — unchanged from before this migration.
        # ON COMMIT DROP ties the temp table's lifetime to this txn (safe for
        # the per-agency analyze loop on one connection); ANALYZE gives the
        # planner stats for the downstream GROUP BYs.
        ch_sql = build_dedup_ch_sql(include_captured_at=True)
        # Column order must match build_dedup_ch_sql's SELECT list exactly:
        # route_code, service_type, scheduled_time, trip_id, date, stop_sequence, dep_delay, captured_at
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _analyze_deduped")
            cur.execute(
                """
                CREATE TEMP TABLE _analyze_deduped (
                    route_code text, service_type text, scheduled_time time,
                    trip_id text, date date, stop_sequence int, dep_delay int,
                    captured_at timestamptz
                ) ON COMMIT DROP
                """
            )
            # `query_row_block_stream` (not `query`) so we never hold the whole
            # dedup result in memory at once. `.query()` buffers the ENTIRE
            # result as a Python list (`result_rows`) before returning it, and
            # the tzinfo fixup below used to build a SECOND full-size list from
            # that — two live copies of a set measured at 5.3M rows / 1.6-3.4GB
            # for agency 8. Streaming yields one block (a list of row-tuples)
            # at a time, so peak memory is bounded by one block, not the whole
            # table. Each block is tzinfo-fixed and INSERTed independently;
            # `execute_values`'s own page_size=10_000 chunking of the Postgres
            # side is unaffected by how the ClickHouse side is fetched.
            with ch_client.query_row_block_stream(ch_sql, parameters={"agency_id": agency_id}) as stream:
                for block in stream:
                    if not block:
                        continue
                    # clickhouse-connect returns DateTime64(0, 'UTC') columns as
                    # NAIVE Python datetimes (its default tz_mode is
                    # "naive_utc") that mean UTC. psycopg2 sends a naive
                    # datetime to Postgres as a plain literal, which Postgres
                    # then interprets in the SESSION's timezone — and every
                    # real analyze() caller (gtfs_pipeline._get_conn, the cron
                    # endpoint) pins `SET TIME ZONE 'Asia/Tokyo'`. Without this
                    # fixup, a ClickHouse timestamp that's naive-but-means-UTC
                    # would get reinterpreted as JST and land 9h early. Same
                    # guard as pipeline/clickhouse.py's max_captured_at /
                    # max_captured_at_before. captured_at is the last element
                    # of each row (see build_dedup_ch_sql's SELECT list above).
                    rows = [
                        (
                            *r[:-1],
                            r[-1].replace(tzinfo=timezone.utc)
                            if r[-1] is not None and r[-1].tzinfo is None
                            else r[-1],
                        )
                        for r in block
                    ]
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO _analyze_deduped VALUES %s",
                        rows,
                        page_size=10_000,
                    )
            cur.execute("ANALYZE _analyze_deduped")

        # ── agg_route_stats ──────────────────────────────────────────────
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL),
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
        _insert_agg(
            "agg_route_stats",
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
        logger.info(f"  agg_route_stats: {len(rows)} rows")

        # ── agg_route_hour ───────────────────────────────────────────────
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL),
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
        _insert_agg(
            "agg_route_hour",
            ["agency_id", "route_code", "service_type", "scheduled_time", "avg_min", "p50_min", "p90_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_route_hour: {len(rows)} rows")

        # ── agg_route_dow ────────────────────────────────────────────────
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL)
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
        _insert_agg(
            "agg_route_dow",
            ["agency_id", "route_code", "service_type", "dow", "avg_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_route_dow: {len(rows)} rows")

        # ── agg_route_hour_dow ───────────────────────────────────────────
        # Per route × service × day-of-week × hour, for the Forecast heatmap.
        # `scheduled_time IS NOT NULL` guards the NOT NULL `hour` column: a typed
        # row lacking a scheduled time would otherwise yield a NULL hour and abort
        # the whole-agency analyze transaction.
        sql = """
            WITH deduped AS (
                SELECT * FROM _analyze_deduped
                WHERE service_type IS NOT NULL AND scheduled_time IS NOT NULL
            )
            SELECT
                %(agency_id)s AS agency_id,
                route_code, service_type,
                EXTRACT(ISODOW FROM date::date)::smallint AS dow,
                EXTRACT(HOUR FROM scheduled_time)::smallint AS hour,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples
            FROM deduped
            GROUP BY route_code, service_type,
                     EXTRACT(ISODOW FROM date::date), EXTRACT(HOUR FROM scheduled_time)
            ORDER BY route_code
        """
        rows = _run_query(sql, p, conn)
        _insert_agg(
            "agg_route_hour_dow",
            ["agency_id", "route_code", "service_type", "dow", "hour", "avg_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_route_hour_dow: {len(rows)} rows")

        # ── agg_daily_trend ──────────────────────────────────────────────
        # UNTYPED dedup so NULL-service routes (e.g. 広島's unmatched rows) are
        # kept — the reports (dow/compare/trend) and overview that read this
        # table never filtered them on their live paths. NULL is coalesced to ''
        # for the NOT NULL column; GROUP BY the same COALESCE (not the raw
        # column) so NULL and '' can't split into duplicate keys. Readers that
        # surface service_type map '' back to None; the service-split panels
        # naturally ignore '' (no 平日/土日祝 match).
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped)
            SELECT
                %(agency_id)s AS agency_id,
                date::text, route_code,
                COALESCE(service_type, '') AS service_type,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """
        rows = _run_query(sql, p, conn)
        _insert_agg(
            "agg_daily_trend",
            ["agency_id", "date", "route_code", "service_type", "avg_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_daily_trend: {len(rows)} rows")

        # ── agg_route_daily (per-route, per-day; powers the fast today/route-summary) ──
        # Mirrors the route-summary endpoint's aggregation but precomputed for
        # every day, so the endpoint reads one tiny row-set for the latest date
        # instead of scanning raw `updates` (which the planner mis-estimates).
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped)
            SELECT
                %(agency_id)s AS agency_id,
                date::text, route_code,
                COALESCE(service_type, '') AS service_type,
                ROUND(AVG(dep_delay))::int AS avg_delay_sec,
                MAX(dep_delay)             AS worst_delay_sec,
                COUNT(DISTINCT trip_id)    AS trips_observed,
                COUNT(*)                   AS samples,
                MAX(captured_at)           AS last_seen_at
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """
        rows = _run_query(sql, p, conn)
        _insert_agg(
            "agg_route_daily",
            [
                "agency_id",
                "date",
                "route_code",
                "service_type",
                "avg_delay_sec",
                "worst_delay_sec",
                "trips_observed",
                "samples",
                "last_seen_at",
            ],
            rows,
            conn,
        )
        logger.info(f"  agg_route_daily: {len(rows)} rows")

        # ── agg_route_daily_dist (per-day delay distribution for reports) ──
        # Exact scalars (sum/count, threshold counts) + a fixed-width delay
        # histogram so range-scoped ranking/on_time/worst_5min read this tiny
        # table instead of scanning raw `updates`. UNTYPED dedup keeps
        # NULL-service routes (the live reports never filtered them); NULL is
        # coalesced to '' in the inner CTE so GROUP BY service_type — and the
        # NOT NULL PK — see the sentinel, never a raw NULL/'' split.
        sql = f"""
            WITH deduped AS (SELECT * FROM _analyze_deduped),
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
                COUNT(*) FILTER (WHERE dep_delay <= 60)  AS on_time_count,
                COUNT(*) FILTER (WHERE dep_delay > 300)  AS late5_count,
                {_HIST_ARRAY}                         AS hist
            FROM bucketed
            GROUP BY date, route_code, service_type
            ORDER BY date, route_code
        """
        rows = _run_query(sql, p, conn)
        _insert_agg(
            "agg_route_daily_dist",
            [
                "agency_id",
                "date",
                "route_code",
                "service_type",
                "samples",
                "sum_delay_sec",
                "on_time_count",
                "late5_count",
                "hist",
            ],
            rows,
            conn,
        )
        logger.info(f"  agg_route_daily_dist: {len(rows)} rows")

        # ── agg_hour_daily (per-day, per-hour-of-day across all routes) ──
        # Powers Overview's peak-hour-by-DOW (its ~96% cold cost). UNTYPED dedup
        # (all observations, no service filter) since that panel aggregates
        # hour-of-day across every route; a service/route filter falls back to
        # the live path on read. (The reports/trend hourly heatmap is the same
        # grain and a natural future consumer, but is not wired here yet.)
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped)
            SELECT
                %(agency_id)s AS agency_id,
                date,
                EXTRACT(HOUR FROM scheduled_time)::smallint AS hour,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples
            FROM deduped
            WHERE scheduled_time IS NOT NULL
            GROUP BY date, EXTRACT(HOUR FROM scheduled_time)
            ORDER BY date, hour
        """
        rows = _run_query(sql, p, conn)
        _insert_agg(
            "agg_hour_daily",
            ["agency_id", "date", "hour", "avg_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_hour_daily: {len(rows)} rows")

        # ── agg_stop_seq ─────────────────────────────────────────────────
        if has_static:
            sql = """
                WITH deduped AS (SELECT * FROM _analyze_deduped)
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
            sql = """
                WITH deduped AS (SELECT * FROM _analyze_deduped)
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
        _insert_agg(
            "agg_stop_seq",
            ["agency_id", "route_code", "stop_sequence", "stop_name", "avg_min", "samples"],
            rows,
            conn,
        )
        logger.info(f"  agg_stop_seq: {len(rows)} rows")

        # ── agg_feed_health (per-day data-quality signal) ────────────────
        # Per-day raw observation count and how many were implausible (frozen/
        # stale TripUpdate spikes, |dep_delay| > MAX_PLAUSIBLE_DELAY_SEC — the same
        # rows the dedup clamp drops). Persisted (not just logged) so the app can
        # surface a feed-health banner. Agency-wide — does NOT require static data,
        # so it runs outside the has_static block. Pure aggregation (no static-table
        # JOIN), so it queries ClickHouse directly and bulk-loads the small per-day
        # result into Postgres. toDate(captured_at, 'Asia/Tokyo'), NOT bare
        # toDate() — same JST-not-UTC reasoning as everywhere else in this
        # migration (see build_dedup_ch_sql's docstring in pipeline/db.py).
        ch_feed_health = ch_client.query(
            """
            SELECT toDate(captured_at, 'Asia/Tokyo') AS date,
                   count() AS raw_samples,
                   countIf(abs(dep_delay) > {max_delay:Int32}) AS clamp_count
            FROM updates
            WHERE agency_id = {agency_id:UInt16} AND dep_delay IS NOT NULL
            GROUP BY date
            """,
            parameters={"agency_id": agency_id, "max_delay": p["max_delay"]},
        )
        with conn.cursor() as cur:
            if ch_feed_health.result_rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES %s",
                    [(agency_id, *row) for row in ch_feed_health.result_rows],
                )
            logger.info(f"  agg_feed_health: {cur.rowcount} rows")
            cur.execute(
                "SELECT COALESCE(SUM(clamp_count), 0) FROM agg_feed_health WHERE agency_id = %(agency_id)s",
                p,
            )
            clamped = cur.fetchone()[0]
            if clamped:
                logger.info(
                    f"  delay clamp: excluded {clamped} implausible observation(s) (|delay| > {p['max_delay']}s)"
                )

        # ── agg_stop_daily (per-stop, per-day delay; powers the heatmap) ──
        # Reads the deduped temp (one row per trip-stop event, latest estimate,
        # already clamped via build_dedup_inner_sql) — NOT raw `updates`. So
        # `samples` counts observations, not feed polls (a frozen/heavily-polled
        # trip no longer inflates the count), and the per-stop mean matches the
        # reports/route-summary surfaces, which read the same deduped set.
        if has_static:
            # Same expr in SELECT and GROUP BY — one call keeps them in sync.
            # Built UNTYPED like agg_route_stop_daily below: NULL service_type is
            # kept as a '' sentinel (COALESCE), not filtered out, so a stop whose
            # traffic is entirely NULL-service still shows on the default (no
            # route filter) heatmap instead of silently reading as zero activity.
            # COALESCE repeated in GROUP BY so the grouped column binds the
            # sentinel, not the raw NULL.
            band_case = time_band_case_sql("d.scheduled_time")
            sql = f"""
                WITH deduped AS (SELECT * FROM _analyze_deduped)
                INSERT INTO agg_stop_daily
                    (agency_id, stop_id, date, service_type, time_band, delay_sum, samples)
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
            """
            with conn.cursor() as cur:
                cur.execute(sql, p)
                logger.info(f"  agg_stop_daily: {cur.rowcount} rows")

            # ── agg_stop_routes (distinct observed routes per stop) ──────────────
            # Needs a JOIN against Postgres static_stop_times, so — mirroring the
            # _analyze_deduped pattern — fetch the distinct raw keys from
            # ClickHouse and bulk-load them into a small Postgres TEMP TABLE,
            # then run the JOIN against that instead of raw `updates`. It's a
            # DISTINCT route_code set, unaffected by poll duplication, and
            # doesn't read dep_delay, so no clamp/dedup is needed on this side.
            ch_keys = ch_client.query(
                "SELECT DISTINCT route_code, trip_id, stop_sequence FROM updates WHERE agency_id = {agency_id:UInt16}",
                parameters={"agency_id": agency_id},
            )
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS _analyze_raw_keys")
                cur.execute(
                    "CREATE TEMP TABLE _analyze_raw_keys (route_code text, trip_id text, stop_sequence int) "
                    "ON COMMIT DROP"
                )
                if ch_keys.result_rows:
                    psycopg2.extras.execute_values(
                        cur, "INSERT INTO _analyze_raw_keys VALUES %s", ch_keys.result_rows, page_size=10_000
                    )
                sql = """
                    INSERT INTO agg_stop_routes (agency_id, stop_id, route_codes)
                    SELECT %(agency_id)s, sst.stop_id,
                           string_agg(DISTINCT k.route_code, ',' ORDER BY k.route_code)
                    FROM _analyze_raw_keys k
                    JOIN static_stop_times sst
                      ON sst.agency_id = %(agency_id)s
                     AND sst.trip_id = k.trip_id
                     AND sst.stop_sequence = k.stop_sequence
                    GROUP BY sst.stop_id
                """
                cur.execute(sql, p)
                logger.info(f"  agg_stop_routes: {cur.rowcount} rows")

            # ── agg_route_stop_daily (per-route-per-stop; powers route-filtered heatmap) ──
            # Same deduped source as agg_stop_daily, plus route_code in the grain.
            # Built UNTYPED: NULL service_type is kept as '' sentinel (reads the full
            # deduped set, no service_type filter) so a route's NULL-service rows still
            # show. COALESCE repeated in GROUP BY so the grouped column binds the
            # sentinel, not the raw NULL.
            band_case = time_band_case_sql("d.scheduled_time")
            sql = f"""
                WITH deduped AS (SELECT * FROM _analyze_deduped)
                INSERT INTO agg_route_stop_daily
                    (agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples)
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
            """
            with conn.cursor() as cur:
                cur.execute(sql, p)
                logger.info(f"  agg_route_stop_daily: {cur.rowcount} rows")

        # ── agg_meta: audit record of this build (NOT load-bearing) ──────
        # Upserted (not in the DELETE/rebuild loop) — one row per agency.
        # The freshness gate derives staleness from the aggs themselves; this
        # only answers "when was this agency last analyzed". `updates` now
        # lives in ClickHouse, so this reuses the same max_captured_at helper
        # Task 4 built and Task 5 already uses elsewhere (pipeline/freshness.py).
        max_cap = ch_max_captured_at(ch_client, agency_id)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agg_meta (agency_id, analyzed_at, max_updates_captured_at) "
                "VALUES (%s, now(), %s) "
                "ON CONFLICT (agency_id) DO UPDATE SET "
                "analyzed_at = EXCLUDED.analyzed_at, "
                "max_updates_captured_at = EXCLUDED.max_updates_captured_at",
                (agency_id, max_cap),
            )

        conn.commit()
        logger.info("Analysis complete.")
    except Exception:
        conn.rollback()
        raise
