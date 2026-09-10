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
- agg_service_delivered_daily — per-day non-executed trip count (executed-vs-planned rate; static_join agencies only)
- agg_route_headway    — scheduled-headway median + high-frequency classification + scheduled mean-wait
  per route (static GTFS only)
- agg_route_headway_daily — per-day reconstructed ACTUAL headway median + sufficient statistics for
  excess-wait/CoV/long-gap pooling (static_join agencies only)
- agg_route_daily_dwell_run — per-day dwell/running-time distribution (decomposition view; static_join agencies only)
- agg_schedule_revision_daily — per-day dominant static_version_id (schedule-revision boundary markers)
- agg_static_version_summary — per-static-version planned trip count / vehicle-km (UPSERT-only; see its own
  section below for why it's exempt from the wipe-and-rewrite loop every other table here follows)
- agg_meta             — audit row: last analyze() time per agency (forensic-only, not load-bearing)

None of the builders below gate a group out at insert time by its sample
count, however thin. Every row carries its own `samples` column instead, so a
low-sample row still exists for a reader to weight, pool across a coarser
grain, or flag as low-confidence (see api.triage.LOW_CONFIDENCE_SAMPLES) —
rather than silently vanishing below some insert-time threshold that varies
table to table.

agg_route_stats / agg_route_hour / agg_route_dow / agg_route_hour_dow /
agg_daily_trend / agg_route_daily / agg_hour_daily each carry a
`sum_delay_sec` column alongside their pre-rounded `avg_min` (or, for
agg_route_daily, `avg_delay_sec`) — the exact SUM(dep_delay) in seconds
behind that mean. A reader pooling MULTIPLE rows of one of these tables must
divide SUM(sum_delay_sec) / SUM(samples) once, at the end, rather than
re-weighting the already-rounded avg_min/avg_delay_sec (SUM(avg_min *
samples) / SUM(samples)) — the latter pools a mean of ROUNDED per-row
values, which is not the same as the true pooled mean over the underlying
raw observations. agg_route_daily_dist already followed this pattern from
the start (see its own `sum_delay_sec`); these seven tables now match it.

agg_daily_trend additionally carries `sum_late_sec`, the exact
SUM(GREATEST(dep_delay, 0)) behind each row — a clamped TOTAL, not a mean, so
a reader pooling multiple rows sums it directly (no division). A route's
total lateness contribution must be computed by summing this per-observation
clamped value, never by clamping an already-pooled avg_min/sum_delay_sec to
zero first: a day (or route) whose trips mix early and late running can
average out to near zero, which would silently zero out that day's real
lateness contribution instead of counting it.
"""

import logging
from collections import defaultdict
from datetime import timezone
from statistics import median

import psycopg2.extras

from api.range import time_band_case_sql
from pipeline.clickhouse import max_captured_at as ch_max_captured_at
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, _static_loaded, build_dedup_ch_sql, hms_to_sec_sql
from pipeline.dwell_run import DWELL_HI, DWELL_LO, DWELL_WIDTH, RUN_HI, RUN_LO, RUN_WIDTH
from pipeline.headways import HIGH_FREQUENCY_HEADWAY_SEC, count_long_gaps, reconstruct_headways
from pipeline.histogram import (
    HI,
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    LO,
    N_BUCKETS,
    WIDTH,
    bucket_case_sql,
    hist_array_sql,
)
from pipeline.strategies.static_join import RT_INGEST_STRATEGIES

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
    "agg_service_delivered_daily",
    "agg_route_headway",
    "agg_route_headway_daily",
    "agg_route_daily_dwell_run",
    "agg_schedule_revision_daily",
)
_VALID_AGG_TABLES = frozenset(_AGG_TABLES_ORDERED)
# agg_static_version_summary is NOT in _AGG_TABLES_ORDERED: it is UPSERTed
# (never wiped) so a past static-feed version's planned-trip-count/vehicle-km
# survives `static_loader.load_static()` overwriting the raw static_* tables
# on the next reload — see the migration's own docstring and the dedicated
# section below for why deleting it every run would defeat its purpose.


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


def _build_and_insert(sql: str, table: str, col_names: list, p: dict, conn) -> None:
    """Run *sql*, insert the result into *table*, and log the row count.

    Shared by the query/dedup/rank-style aggregate builders below, which all
    follow the same run-then-insert-then-log shape.
    """
    rows = _run_query(sql, p, conn)
    _insert_agg(table, col_names, rows, conn)
    logger.info(f"  {table}: {len(rows)} rows")


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
        ch_sql = build_dedup_ch_sql(include_captured_at=True, include_arr_delay=True)
        # Column order must match build_dedup_ch_sql's SELECT list exactly:
        # route_code, service_type, scheduled_time, trip_id, date, stop_sequence,
        # dep_delay, last_captured_at, arr_delay (arr_delay is always last
        # regardless of include_captured_at -- see build_dedup_ch_sql's docstring).
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _analyze_deduped")
            cur.execute(
                """
                CREATE TEMP TABLE _analyze_deduped (
                    route_code text, service_type text, scheduled_time time,
                    trip_id text, date date, stop_sequence int, dep_delay int,
                    captured_at timestamptz, arr_delay int
                ) ON COMMIT DROP
                """
            )
            # `query_row_block_stream` (not `query`) so we never hold the whole
            # dedup result in memory at once. `.query()` buffers the ENTIRE
            # result as a Python list (`result_rows`) before returning it, and
            # the tzinfo fixup below used to build a SECOND full-size list from
            # that — two live copies of a set that scales with the agency's
            # total row count. Streaming yields one block (a list of row-tuples)
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
                    # max_captured_at_before. last_captured_at is the
                    # second-to-last element of each row -- arr_delay is
                    # always last regardless of include_captured_at (see
                    # build_dedup_ch_sql's SELECT list above).
                    rows = [
                        (
                            *r[:-2],
                            r[-2].replace(tzinfo=timezone.utc) if r[-2] is not None and r[-2].tzinfo is None else r[-2],
                            r[-1],
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
        # No minimum-sample HAVING here — see the module docstring's no-gate
        # policy.
        # on_time_pct_raw/late_5min_plus/late5_pct_raw bake the legacy_60s
        # preset's fixed thresholds (LEGACY_ON_TIME_LATE_TOLERANCE_SEC=60,
        # LEGACY_SEVERE_LATE_TOLERANCE_SEC=300) into these exact scalar
        # columns — the only preset analyze() materialises. A caller wanting
        # a different on-time/late tolerance reads agg_route_daily_dist's
        # `hist` column instead and estimates it at query time (see
        # pipeline.histogram.count_in_range /
        # pipeline.reports.rankings.compute_on_time), rather than needing a
        # re-aggregation for every tolerance someone might ask for.
        on_time_thr = LEGACY_ON_TIME_LATE_TOLERANCE_SEC
        late_thr = LEGACY_SEVERE_LATE_TOLERANCE_SEC
        sql = f"""
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL),
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
                    -- ClickHouse fallback, which intentionally reproduces the
                    -- old min-rank tie formula this column used to use — see
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
        """
        _build_and_insert(
            sql,
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
                "sum_delay_sec",
            ],
            p,
            conn,
        )

        # ── agg_route_hour ───────────────────────────────────────────────
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL),
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
        """
        _build_and_insert(
            sql,
            "agg_route_hour",
            [
                "agency_id",
                "route_code",
                "service_type",
                "scheduled_time",
                "avg_min",
                "p50_min",
                "p90_min",
                "samples",
                "sum_delay_sec",
            ],
            p,
            conn,
        )

        # ── agg_route_dow ────────────────────────────────────────────────
        sql = """
            WITH deduped AS (SELECT * FROM _analyze_deduped WHERE service_type IS NOT NULL)
            SELECT
                %(agency_id)s AS agency_id,
                route_code, service_type,
                EXTRACT(ISODOW FROM date::date)::smallint AS dow,
                ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec
            FROM deduped
            GROUP BY route_code, service_type, EXTRACT(ISODOW FROM date::date)
            ORDER BY route_code
        """
        _build_and_insert(
            sql,
            "agg_route_dow",
            ["agency_id", "route_code", "service_type", "dow", "avg_min", "samples", "sum_delay_sec"],
            p,
            conn,
        )

        # ── agg_route_hour_dow ───────────────────────────────────────────
        # Per route × service × day-of-week × hour, for the Forecast heatmap.
        # `scheduled_time IS NOT NULL` guards the NOT NULL `hour` column: a typed
        # row lacking a scheduled time would otherwise yield a NULL hour and abort
        # the whole-agency analyze transaction. `EXTRACT(HOUR FROM scheduled_time)`
        # is always 0-23: an hour >= 24 (GTFS's after-midnight departure_time
        # notation, e.g. "25:30:00") is rejected at ingest time (see
        # pipeline/strategies/_time.py) and never reaches `_analyze_deduped`, so
        # a late-night continuation trip is absent from this aggregate rather
        # than folded into the early-morning bucket.
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
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec
            FROM deduped
            GROUP BY route_code, service_type,
                     EXTRACT(ISODOW FROM date::date), EXTRACT(HOUR FROM scheduled_time)
            ORDER BY route_code
        """
        _build_and_insert(
            sql,
            "agg_route_hour_dow",
            ["agency_id", "route_code", "service_type", "dow", "hour", "avg_min", "samples", "sum_delay_sec"],
            p,
            conn,
        )

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
                COUNT(*) AS samples,
                SUM(dep_delay) AS sum_delay_sec,
                SUM(GREATEST(dep_delay, 0)) AS sum_late_sec
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """
        _build_and_insert(
            sql,
            "agg_daily_trend",
            [
                "agency_id",
                "date",
                "route_code",
                "service_type",
                "avg_min",
                "samples",
                "sum_delay_sec",
                "sum_late_sec",
            ],
            p,
            conn,
        )

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
                MAX(captured_at)           AS last_seen_at,
                SUM(dep_delay)             AS sum_delay_sec
            FROM deduped
            GROUP BY date, route_code, COALESCE(service_type, '')
            ORDER BY date, route_code
        """
        _build_and_insert(
            sql,
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
                "sum_delay_sec",
            ],
            p,
            conn,
        )

        # ── agg_route_daily_dist (per-day delay distribution for reports) ──
        # Exact scalars (sum/count, threshold counts) + a fixed-width delay
        # histogram so range-scoped ranking/on_time/worst_5min read this tiny
        # table instead of scanning raw `updates`. UNTYPED dedup keeps
        # NULL-service routes (the live reports never filtered them); NULL is
        # coalesced to '' in the inner CTE so GROUP BY service_type — and the
        # NOT NULL PK — see the sentinel, never a raw NULL/'' split.
        # on_time_count/late5_count bake the legacy_60s preset's fixed
        # thresholds — see the identical rationale on agg_route_stats above.
        # A caller wanting a different tolerance reads `hist` instead (same
        # column this table already stores for p50/p90 interpolation).
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
                COUNT(*) FILTER (WHERE dep_delay <= {LEGACY_ON_TIME_LATE_TOLERANCE_SEC})  AS on_time_count,
                COUNT(*) FILTER (WHERE dep_delay > {LEGACY_SEVERE_LATE_TOLERANCE_SEC})  AS late5_count,
                {_HIST_ARRAY}                         AS hist
            FROM bucketed
            GROUP BY date, route_code, service_type
            ORDER BY date, route_code
        """
        _build_and_insert(
            sql,
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
            p,
            conn,
        )

        # ── agg_hour_daily (per-day, per-hour-of-day across all routes) ──
        # Powers Overview's peak-hour-by-DOW (its dominant cold-load cost) and
        # the reports/trend hourly heatmap (same grain). UNTYPED dedup (all
        # observations, no service filter) since both readers aggregate
        # hour-of-day across every route; a service/route filter falls back to
        # the live path on read. `sum_delay_sec` lets a caller that pools
        # MULTIPLE rows of this table (e.g. the trend heatmap's
        # dow×band grid) divide an exact raw-seconds total once, at the end,
        # instead of re-weighting this row's own already-rounded `avg_min`.
        # `EXTRACT(HOUR FROM scheduled_time)` is always 0-23, same invariant as
        # the agg_route_hour_dow comment above (see pipeline/strategies/_time.py).
        sql = """
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
        """
        _build_and_insert(
            sql,
            "agg_hour_daily",
            ["agency_id", "date", "hour", "avg_min", "samples", "sum_delay_sec"],
            p,
            conn,
        )

        # ── agg_stop_seq ─────────────────────────────────────────────────
        # has_static branches the stop_name source: with static GTFS loaded,
        # join static_stop_times/static_stops for the real name; without it,
        # synthesize a numbered placeholder ("N番停留所") from stop_sequence
        # alone, since there's no other source of stop names to key on.
        # No minimum-sample HAVING here either — see the module docstring's
        # no-gate policy.
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
                ORDER BY ROUND(AVG(dep_delay)/60.0::numeric, 2) DESC
            """
        _build_and_insert(
            sql,
            "agg_stop_seq",
            ["agency_id", "route_code", "stop_sequence", "stop_name", "avg_min", "samples"],
            p,
            conn,
        )

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
            # len(result_rows), not cur.rowcount: when ClickHouse returns no
            # rows, no statement runs on this cursor, so cur.rowcount is the
            # psycopg2 "nothing executed" sentinel -1 -- misleading in the
            # one operational log line that confirms this builder ran.
            logger.info(f"  agg_feed_health: {len(ch_feed_health.result_rows)} rows")
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
        # already clamped via build_dedup_ch_sql) — NOT raw `updates`. So
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
            #
            # Deliberately NOT derived from _analyze_deduped (a cheaper,
            # single-scan alternative tried and reverted): _analyze_deduped is
            # pre-filtered by the dedup's clamp (`dep_delay IS NOT NULL AND
            # BETWEEN -MAX_PLAUSIBLE_DELAY_SEC AND MAX_PLAUSIBLE_DELAY_SEC`),
            # so a (route_code, trip_id, stop_sequence) whose every observation
            # was NULL/implausible would silently drop out of stop coverage —
            # a real, non-trivial share of keys, enough to lose stops' entire
            # route coverage in practice. `agg_stop_routes` is about which
            # routes serve a stop, independent of whether any of those
            # observations happened to carry a numeric delay — the extra,
            # non-trivial ClickHouse round-trip (a second full scan of the
            # agency's keys, not a cheap add-on) is the correctness-over-perf
            # trade this table's semantics require.
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS _analyze_raw_keys")
                cur.execute(
                    "CREATE TEMP TABLE _analyze_raw_keys (route_code text, trip_id text, stop_sequence int) "
                    "ON COMMIT DROP"
                )
                # `query_row_block_stream` (not `query`), same rationale as
                # _analyze_deduped above: this key set is deliberately LARGER
                # than _analyze_deduped by design (that's the correctness fix
                # this block exists for), so buffering the whole thing in
                # `.query()`'s result_rows would hit the same unbounded-memory
                # shape that streaming was introduced to eliminate 40 lines
                # up, just for a bigger set.
                ch_keys_sql = (
                    "SELECT DISTINCT route_code, trip_id, stop_sequence FROM updates "
                    "WHERE agency_id = {agency_id:UInt16}"
                )
                with ch_client.query_row_block_stream(ch_keys_sql, parameters={"agency_id": agency_id}) as stream:
                    for block in stream:
                        if not block:
                            continue
                        psycopg2.extras.execute_values(
                            cur, "INSERT INTO _analyze_raw_keys VALUES %s", block, page_size=10_000
                        )
                # The planner sizes a freshly created temp table at a few
                # thousand rows (reltuples=0) regardless of how many rows it
                # actually holds, and this table drives the join below against
                # static_stop_times -- ANALYZE gives the planner real stats to
                # pick a join strategy from, same as _analyze_deduped gets.
                cur.execute("ANALYZE _analyze_raw_keys")
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

            # ── agg_route_headway (scheduled-headway classification) ─────
            # Supports classifying routes by scheduled frequency for later
            # filtering. Derived purely from the static GTFS schedule
            # (static_stop_times/static_trips/
            # static_routes), independent of any RT history -- unlike
            # agg_route_headway_daily below, this needs no ingest_strategy
            # gate; every agency with a static feed loaded (has_static) gets
            # a row for every route_code its schedule resolves to.
            #
            # route_code (RT's `updates.route_code`) is matched to the static
            # schedule's route_id via the SAME digit-suffix regex used
            # everywhere else this codebase bridges the two id spaces
            # (api/routers/static.py, pipeline/reports/overview.py's
            # `_route_short_names`) -- route_id itself IS the route_code for
            # a feed whose route_id carries no trailing "(NNNN)"
            # (regexp_replace no-ops on a non-matching input).
            #
            # A route can run under more than one GTFS service_id (weekday
            # vs. weekend calendars, etc.) with genuinely different
            # headways; mixing both into one sorted sequence of departure
            # times would interleave two independent schedules and produce a
            # spuriously DENSER (smaller-gap) blend than either calendar
            # alone. Instead, for each route this picks the single
            # service_id with the most distinct trips (its dominant,
            # most-typical calendar) and derives the median from that
            # calendar's departures only.
            #
            # Headway gaps are pooled across every stop_id the route calls
            # at (not just one "representative" stop) before taking the
            # median -- avoids having to justify picking one stop as
            # representative, and a consistently-spaced route has a similar
            # gap distribution at every stop it serves. A zero-second gap
            # (two trips scheduled for the exact same departure_time at the
            # same stop -- typically a multi-berth stop or a data artifact,
            # not real zero-headway service) is excluded rather than let it
            # drag the median down.
            #
            # departure_time is raw GTFS text ("H:MM:SS" or similar) --
            # unlike `updates.scheduled_time` (normalized at ingest time by
            # pipeline.strategies._time.normalize_departure_time and capped
            # to same-day hours), static_stop_times stores it completely
            # unvalidated, so this filters to the strict numeric "H+:MM:SS"
            # shape before splitting on ':' and summing to seconds-of-day
            # (deliberately NOT capped at 24h -- GTFS's
            # post-midnight-continuation hours like "25:30:00" are valid
            # schedule data and must not raise or misparse; only a
            # non-numeric/malformed shape is excluded).
            hf_thr = HIGH_FREQUENCY_HEADWAY_SEC
            sql = f"""
                WITH route_map AS (
                    SELECT route_id, regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code
                    FROM static_routes WHERE agency_id = %(agency_id)s
                ),
                trips_with_route AS (
                    SELECT t.trip_id, t.service_id, rm.route_code
                    FROM static_trips t
                    JOIN route_map rm ON rm.route_id = t.route_id
                    WHERE t.agency_id = %(agency_id)s
                ),
                service_trip_counts AS (
                    SELECT route_code, service_id, COUNT(DISTINCT trip_id) AS trip_count
                    FROM trips_with_route
                    GROUP BY route_code, service_id
                ),
                dominant_service AS (
                    SELECT DISTINCT ON (route_code) route_code, service_id
                    FROM service_trip_counts
                    ORDER BY route_code, trip_count DESC, service_id
                ),
                scheduled_departures AS (
                    SELECT twr.route_code, sst.stop_id,
                        (split_part(sst.departure_time, ':', 1))::int * 3600
                      + (split_part(sst.departure_time, ':', 2))::int * 60
                      + (split_part(sst.departure_time, ':', 3))::int AS dep_sec
                    FROM trips_with_route twr
                    JOIN dominant_service ds
                      ON ds.route_code = twr.route_code AND ds.service_id = twr.service_id
                    JOIN static_stop_times sst
                      ON sst.agency_id = %(agency_id)s AND sst.trip_id = twr.trip_id
                    WHERE sst.departure_time ~ '^[0-9]+:[0-5][0-9]:[0-5][0-9]$'
                ),
                gaps AS (
                    SELECT route_code,
                           dep_sec - LAG(dep_sec) OVER (PARTITION BY route_code, stop_id ORDER BY dep_sec)
                               AS headway_sec
                    FROM scheduled_departures
                ),
                medians AS (
                    SELECT route_code,
                           PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY headway_sec) AS scheduled_headway_median_sec,
                           COUNT(*) AS scheduled_samples,
                           -- E[H^2] / (2*E[H]) over this route's scheduled headway
                           -- distribution -- item 94's mean-wait-time formula
                           -- (pipeline.headways.mean_wait_from_moments), computed
                           -- here in SQL instead of pulling every raw headway_sec
                           -- back into Python. NULLIF guards the (already
                           -- near-impossible, since headway_sec > 0 is filtered
                           -- below) zero-mean case.
                           AVG(headway_sec::float8 * headway_sec::float8)
                               / NULLIF(2 * AVG(headway_sec::float8), 0) AS scheduled_wait_mean_sec
                    FROM gaps
                    WHERE headway_sec IS NOT NULL AND headway_sec > 0
                    GROUP BY route_code
                )
                SELECT %(agency_id)s AS agency_id, route_code,
                       scheduled_headway_median_sec, scheduled_samples,
                       scheduled_headway_median_sec <= {hf_thr} AS is_high_frequency,
                       scheduled_wait_mean_sec
                FROM medians
            """
            _build_and_insert(
                sql,
                "agg_route_headway",
                [
                    "agency_id",
                    "route_code",
                    "scheduled_headway_median_sec",
                    "scheduled_samples",
                    "is_high_frequency",
                    "scheduled_wait_mean_sec",
                ],
                p,
                conn,
            )

        # ── agg_service_delivered_daily (per-day non-executed trip count) ──
        # Powers pipeline.reports.service_delivered's executed/planned ratio
        # without a live per-request ClickHouse scan over `updates`. Agency-wide
        # and independent of has_static above (a static schedule loaded only
        # matters to the READ side's planned_trips count) -- pure aggregation
        # queried directly against ClickHouse, same shape as agg_feed_health.
        #
        # Only feeds confirmed to send schedule_relationship_trip/_stop
        # populate this table (today: the static_join ingest strategy;
        # aomori_regex always leaves both columns NULL) -- an agency on any
        # other ingest_strategy is skipped entirely (zero rows here), which the
        # read path distinguishes from "confirmed zero cancellations" via
        # agencies.ingest_strategy, never via row presence in this table.
        # Materialization here is gated on ingest_strategy alone, not on the
        # per-agency probe verdicts in rt_field_coverage_probes (see
        # pipeline.strategies.static_join.rt_field_coverage_confirmed)
        # -- sharing an ingest strategy does not by itself prove a given agency's
        # feed actually populates these fields. Today this is safe because every
        # reader of this table (pipeline.reports.service_delivered) re-applies
        # that confirmed-agency gate before returning data; any new
        # direct reader of agg_service_delivered_daily must do the same or it
        # will treat an unconfirmed agency's rows as trustworthy.
        with conn.cursor() as cur:
            cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
            row = cur.fetchone()
        if row and row[0] in RT_INGEST_STRATEGIES:
            # ClickHouse's argMax(arg, val) silently SKIPS a row whose `arg`
            # is NULL when picking the max -- it does not return NULL just
            # because the true latest (captured_at, file_name) row happens to
            # have a NULL field. A raw `argMax(schedule_relationship_stop,
            # ...)` would therefore still return a stale SKIPPED=1 from an
            # earlier poll even after a later poll corrects that stop back to
            # normal (NULL) -- the later NULL row is invisible to argMax, not
            # merely filtered by an explicit `IS NOT NULL` (removing such a
            # filter alone does not fix this; the column itself must never be
            # NULL going into argMax). `coalesce(..., -1)` maps NULL to a
            # sentinel outside the real value range (0/1/2 per GTFS-RT's
            # ScheduleRelationship enum) so argMax always sees a real value
            # for every row and genuinely reflects the latest observation,
            # including a correction back to "not skipped/canceled". Applied
            # to both subqueries for the same reason, even though today's
            # confirmed-populating feeds always send schedule_relationship_
            # trip on every observation (never NULL) -- this is defensive
            # symmetry, not dead code, since nothing prevents a future feed
            # from sending it more sparingly. No date range filter -- analyze()
            # always covers this agency's full history, same as agg_feed_health
            # and the dedup materialization above. A day with zero non-executed
            # trips has no matching row in the inner UNION, so it emits no row
            # here at all -- the read path sums with a zero default rather than
            # assuming row-per-day density.
            ch_service_delivered = ch_client.query(
                """
                SELECT svc_date, count() FROM (
                    SELECT svc_date, trip_id FROM (
                        SELECT toDate(captured_at, 'Asia/Tokyo') AS svc_date, trip_id,
                               argMax(coalesce(schedule_relationship_trip, -1), (captured_at, file_name)) AS trip_rel
                        FROM updates WHERE agency_id = {agency_id:UInt16}
                        GROUP BY svc_date, trip_id
                    ) WHERE trip_rel = 3
                    UNION DISTINCT
                    SELECT svc_date, trip_id FROM (
                        SELECT toDate(captured_at, 'Asia/Tokyo') AS svc_date, trip_id,
                               argMax(coalesce(schedule_relationship_stop, -1), (captured_at, file_name)) AS stop_rel
                        FROM updates WHERE agency_id = {agency_id:UInt16}
                        GROUP BY svc_date, trip_id, stop_sequence
                    ) WHERE stop_rel = 1
                ) GROUP BY svc_date
                """,
                parameters={"agency_id": agency_id},
            )
            with conn.cursor() as cur:
                if ch_service_delivered.result_rows:
                    psycopg2.extras.execute_values(
                        cur,
                        "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES %s",
                        [(agency_id, *r) for r in ch_service_delivered.result_rows],
                    )
                logger.info(f"  agg_service_delivered_daily: {len(ch_service_delivered.result_rows)} rows")
        else:
            logger.info("  agg_service_delivered_daily: 0 rows (ingest_strategy != static_join)")

        # ── agg_schedule_revision_daily (per-day dominant static_version_id) ──
        # Powers pipeline.reports.schedule_revision's boundary-date detection
        # so a metric change coinciding with a timetable revision isn't
        # misread as a service-quality change. Independent of ingest_strategy
        # (unlike agg_service_delivered_daily) -- any agency whose strategy
        # joins static data at all (today: static_join) can populate this;
        # an agency that never does (aomori_regex) or a day predating item
        # 88's rollout simply has every row's static_version_id NULL, which
        # the inner GROUP BY collapses to its own NULL-keyed group -- the
        # outer HAVING drops that group's day entirely rather than inserting
        # a NULL-version row, since a NULL isn't a "version" a boundary can
        # be drawn against.
        #
        # `argMax(version, cnt)` picks the version stamped on the MOST rows
        # that day (mode), not the version of the latest single observation
        # -- a reload happening mid-day should attribute that day to whichever
        # version actually served most of it, not to whichever the last poll
        # happened to see.
        ch_schedule_revision = ch_client.query(
            """
            SELECT svc_date, argMax(version, cnt) AS static_version_id
            FROM (
                SELECT toDate(captured_at, 'Asia/Tokyo') AS svc_date,
                       static_version_id AS version,
                       count() AS cnt
                FROM updates WHERE agency_id = {agency_id:UInt16}
                GROUP BY svc_date, version
            )
            GROUP BY svc_date
            HAVING static_version_id IS NOT NULL
            """,
            parameters={"agency_id": agency_id},
        )
        with conn.cursor() as cur:
            if ch_schedule_revision.result_rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO agg_schedule_revision_daily (agency_id, date, static_version_id) VALUES %s",
                    [(agency_id, *r) for r in ch_schedule_revision.result_rows],
                )
            logger.info(f"  agg_schedule_revision_daily: {len(ch_schedule_revision.result_rows)} rows")

        # ── agg_route_daily_dwell_run (per-day dwell/running-time distribution) ──
        # Decomposes arr_delay + dep_delay into per-stop-visit dwell time (this
        # visit's departure minus its own arrival) and running time (this
        # visit's arrival minus the PREVIOUS visit's departure) -- see
        # pipeline/dwell_run.py for the shared math this mirrors in SQL. Reads
        # arr_delay straight from _analyze_deduped (materialised once above)
        # rather than running its own second ClickHouse scan -- see that
        # section's own comment for why a second scan is deliberately avoided.
        # Requires BOTH a static schedule (arrival_time/departure_time come
        # from static_stop_times, which `has_static` alone confirms rows
        # exist for) AND an ingest strategy confirmed to send `arr_delay`
        # (today: static_join; reuses `row` from the agg_service_delivered_daily
        # check just above) -- either missing means zero rows here, same
        # "row presence is not the availability signal, ingest_strategy is"
        # convention as agg_service_delivered_daily. Same read-side-only caveat
        # applies: sharing ingest_strategy doesn't imply confirmed field
        # coverage, so pipeline.reports.dwell_run's reader additionally
        # requires a live rt_field_coverage_probes verdict before trusting
        # these rows.
        if has_static and row and row[0] in RT_INGEST_STRATEGIES:
            dwell_bucket_expr = bucket_case_sql("dwell_sec", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
            run_bucket_expr = bucket_case_sql("running_sec", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
            dwell_hist_expr = hist_array_sql("bd", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
            run_hist_expr = hist_array_sql("br", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
            sched_arr_expr = hms_to_sec_sql("sst.arrival_time")
            sched_dep_expr = hms_to_sec_sql("sst.departure_time")
            sql = f"""
                WITH visits AS (
                    SELECT
                        d.route_code, COALESCE(d.service_type, '') AS service_type,
                        d.trip_id, d.date, d.stop_sequence, d.dep_delay, d.arr_delay,
                        {sched_arr_expr} AS sched_arr_sec,
                        {sched_dep_expr} AS sched_dep_sec
                    FROM _analyze_deduped d
                    LEFT JOIN static_stop_times sst
                      ON sst.agency_id = %(agency_id)s
                     AND sst.trip_id = d.trip_id
                     AND sst.stop_sequence = d.stop_sequence
                ),
                actuals AS (
                    -- A stop lacking a static schedule row (LEFT JOIN found no
                    -- match) or lacking arrival_time/departure_time within one
                    -- yields NULL sched_dep_sec/sched_arr_sec here -- every
                    -- such visit is still KEPT (not filtered out) so the
                    -- LAG() window below sees every stop_sequence in order;
                    -- dropping the row would let LAG() silently skip past it
                    -- and pair the FOLLOWING stop with the wrong previous
                    -- departure. actual_dep_sec is NULL exactly when
                    -- sched_dep_sec is NULL. actual_arr_sec additionally needs
                    -- BOTH arr_delay and sched_arr_sec, so it's frequently
                    -- NULL even when actual_dep_sec resolves -- that's the
                    -- "dwell/running needs arr_delay, running's previous-stop
                    -- side only needs dep_delay" split pipeline.dwell_run's
                    -- module docstring describes.
                    SELECT route_code, service_type, trip_id, date, stop_sequence,
                        CASE WHEN sched_dep_sec IS NOT NULL
                             THEN sched_dep_sec + dep_delay END AS actual_dep_sec,
                        CASE WHEN arr_delay IS NOT NULL AND sched_arr_sec IS NOT NULL
                             THEN sched_arr_sec + arr_delay END AS actual_arr_sec
                    FROM visits
                ),
                with_prev AS (
                    SELECT *,
                        LAG(actual_dep_sec) OVER (
                            PARTITION BY trip_id, date ORDER BY stop_sequence
                        ) AS prev_actual_dep_sec
                    FROM actuals
                ),
                computed AS (
                    SELECT
                        route_code, service_type, date,
                        CASE WHEN actual_arr_sec IS NOT NULL
                             THEN actual_dep_sec - actual_arr_sec END AS dwell_sec,
                        CASE WHEN actual_arr_sec IS NOT NULL AND prev_actual_dep_sec IS NOT NULL
                             THEN actual_arr_sec - prev_actual_dep_sec END AS running_sec
                    FROM with_prev
                ),
                bucketed AS (
                    SELECT route_code, service_type, date, dwell_sec, running_sec,
                        {dwell_bucket_expr} AS bd,
                        {run_bucket_expr} AS br
                    FROM computed
                )
                SELECT
                    %(agency_id)s AS agency_id,
                    date::text, route_code, service_type,
                    COUNT(*) FILTER (WHERE dwell_sec IS NOT NULL) AS dwell_samples,
                    COALESCE(SUM(dwell_sec) FILTER (WHERE dwell_sec IS NOT NULL), 0) AS dwell_sum_sec,
                    {dwell_hist_expr} AS hist_dwell,
                    COUNT(*) FILTER (WHERE running_sec IS NOT NULL) AS run_samples,
                    COALESCE(SUM(running_sec) FILTER (WHERE running_sec IS NOT NULL), 0) AS run_sum_sec,
                    {run_hist_expr} AS hist_run
                FROM bucketed
                GROUP BY date, route_code, service_type
                ORDER BY date, route_code
            """
            _build_and_insert(
                sql,
                "agg_route_daily_dwell_run",
                [
                    "agency_id",
                    "date",
                    "route_code",
                    "service_type",
                    "dwell_samples",
                    "dwell_sum_sec",
                    "hist_dwell",
                    "run_samples",
                    "run_sum_sec",
                    "hist_run",
                ],
                p,
                conn,
            )
        else:
            logger.info("  agg_route_daily_dwell_run: 0 rows (needs static schedule + ingest_strategy=static_join)")

        # ── agg_route_headway_daily (per-day reconstructed ACTUAL headway) ──
        # Supports a later excess-wait-time computation (actual vs. scheduled
        # headway). Pooled at the physical stop level, from ClickHouse
        # `updates.stop_id` (see pipeline/headways.py's module docstring for
        # why the physical stop is the correct grouping for pooling across
        # trips), so -- like agg_service_delivered_daily above -- only an
        # ingest strategy that CAN populate stop_id (today: static_join;
        # aomori_regex always leaves it NULL) gets any rows here. Skipped
        # entirely (zero rows) for any other ingest_strategy, same "row
        # presence is not the availability signal, ingest_strategy is"
        # convention as agg_service_delivered_daily. Same read-side-only
        # caveat applies: sharing ingest_strategy doesn't imply confirmed
        # field coverage, so pipeline.reports.headway_quality's reader
        # additionally requires a live rt_field_coverage_probes verdict
        # before trusting these rows.
        with conn.cursor() as cur:
            cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
            row = cur.fetchone()
        if row and row[0] in RT_INGEST_STRATEGIES:
            # One row per (route_code, stop_id, service day) with an ARRAY of
            # that group's actual event times (seconds-of-day, scheduled_time
            # parsed + dep_delay) -- cardinality is bounded by routes × stops
            # × days, not by raw observation count, so it's safe to fetch as
            # one block (ch_client.query(), not the streaming reader
            # `_analyze_deduped` needs) even though the pre-aggregation
            # GROUP BY underneath it scans the agency's full `updates`
            # history. `argMax(..., (captured_at, file_name))` per
            # (route_code, stop_id, date, trip_id, stop_sequence) is the SAME
            # latest-observation-wins dedup rule as build_dedup_ch_sql --
            # stop_sequence stays in this per-event key (matching
            # build_dedup_ch_sql and agg_service_delivered_daily's own
            # stop-level subquery) so a route that revisits the same
            # physical stop_id twice within one trip keeps both visits as
            # distinct events; only the outer SELECT below pools across
            # stop_sequence (and across trips) at the physical-stop level.
            #
            # scheduled_time is normalized "HH:MM[:SS]" text (see
            # pipeline.strategies._time.normalize_departure_time) -- seconds
            # are optional per that normalizer, so the parse below tolerates
            # a 2-element split (defaulting seconds to 0) rather than
            # indexing a possibly-absent third element.
            #
            # `assumeNotNull(scheduled_time)` in the `filtered` CTE is
            # load-bearing, not cosmetic: ClickHouse infers splitByChar's
            # return type from its ARGUMENT's type, and `Array(...)` can
            # never itself be `Nullable` (only its elements can) -- passing
            # the raw `Nullable(String)` `scheduled_time` straight to
            # splitByChar makes the planner try to type the result as
            # `Nullable(Array(String))` and raises `ILLEGAL_TYPE_OF_ARGUMENT`
            # at query-planning time, regardless of whether any row is
            # actually NULL at runtime. The `filtered` CTE's own
            # `scheduled_time IS NOT NULL` WHERE clause makes the
            # assumeNotNull() call runtime-safe; it does nothing to satisfy
            # the planner on its own; splitByChar needs a statically
            # non-Nullable argument type.
            ch_headway = ch_client.query(
                """
                WITH per_event AS (
                    SELECT route_code, stop_id, toDate(captured_at, 'Asia/Tokyo') AS svc_date, trip_id,
                           stop_sequence,
                           argMax(dep_delay, (captured_at, file_name)) AS dep_delay,
                           argMax(scheduled_time, (captured_at, file_name)) AS scheduled_time
                    FROM updates
                    WHERE agency_id = {agency_id:UInt16} AND stop_id IS NOT NULL AND route_code IS NOT NULL
                    GROUP BY route_code, stop_id, svc_date, trip_id, stop_sequence
                ),
                filtered AS (
                    SELECT route_code, stop_id, svc_date, dep_delay,
                           assumeNotNull(scheduled_time) AS scheduled_time_nn
                    FROM per_event
                    WHERE dep_delay IS NOT NULL
                      AND dep_delay BETWEEN {min_delay:Int32} AND {max_delay:Int32}
                      AND scheduled_time IS NOT NULL
                ),
                timed AS (
                    SELECT route_code, stop_id, svc_date,
                           toInt64(dep_delay)
                             + toInt64(splitByChar(':', scheduled_time_nn)[1]) * 3600
                             + toInt64(splitByChar(':', scheduled_time_nn)[2]) * 60
                             + if(length(splitByChar(':', scheduled_time_nn)) >= 3,
                                  toInt64(splitByChar(':', scheduled_time_nn)[3]), 0) AS actual_sec
                    FROM filtered
                    WHERE length(splitByChar(':', scheduled_time_nn)) >= 2
                )
                SELECT route_code, stop_id, svc_date, groupArray(actual_sec) AS times
                FROM timed
                GROUP BY route_code, stop_id, svc_date
                """,
                parameters={
                    "agency_id": agency_id,
                    "min_delay": -MAX_PLAUSIBLE_DELAY_SEC,
                    "max_delay": MAX_PLAUSIBLE_DELAY_SEC,
                },
            )
            # Pool every stop's reconstructed gaps for the same (route_code,
            # date) together -- same rationale as agg_route_headway's static
            # side: a consistently-spaced route has a similar gap
            # distribution at every stop it serves, and pooling avoids
            # having to justify picking one "representative" stop.
            # Zero/negative gaps (a duplicate or out-of-order observation)
            # are excluded, not counted as a real zero-headway event.
            pooled: dict[tuple[object, object], list[float]] = defaultdict(list)
            for route_code, _stop_id, svc_date, times in ch_headway.result_rows:
                pooled[(route_code, svc_date)].extend(g for g in reconstruct_headways(times) if g > 0)
            # Scheduled median per route, read back from agg_route_headway
            # (already INSERTed earlier in this SAME transaction, above) --
            # used only to threshold "long" gaps (item 94). A route with no
            # resolvable scheduled median (never classified/high-frequency)
            # gets long_gap_count=0 via count_long_gaps' own None handling;
            # such a route is never surfaced by the query-time report
            # anyway (see pipeline.reports.headway_quality), so 0 vs. NULL
            # here is not user-visible.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT route_code, scheduled_headway_median_sec FROM agg_route_headway WHERE agency_id = %s",
                    (agency_id,),
                )
                scheduled_median_by_route = dict(cur.fetchall())
            headway_rows = [
                (
                    agency_id,
                    route_code,
                    svc_date,
                    median(gaps),
                    len(gaps),
                    sum(gaps),
                    sum(g * g for g in gaps),
                    count_long_gaps(gaps, scheduled_median_by_route.get(route_code)),
                )
                for (route_code, svc_date), gaps in pooled.items()
                if gaps
            ]
            _insert_agg(
                "agg_route_headway_daily",
                [
                    "agency_id",
                    "route_code",
                    "date",
                    "actual_headway_median_sec",
                    "actual_samples",
                    "actual_headway_sum_sec",
                    "actual_headway_sumsq_sec2",
                    "long_gap_count",
                ],
                headway_rows,
                conn,
            )
            logger.info(f"  agg_route_headway_daily: {len(headway_rows)} rows")
        else:
            logger.info("  agg_route_headway_daily: 0 rows (ingest_strategy != static_join)")
        # ── agg_static_version_summary (per-static-version planned trip count / vehicle-km) ──
        # UPSERTed only for the CURRENTLY loaded static_version_id — see the
        # migration's docstring and _AGG_TABLES_ORDERED's comment above for
        # why this table is deliberately exempt from the wipe-and-rewrite
        # loop every other agg_* table follows: a past version's row must
        # survive `static_loader.load_static()` overwriting the raw
        # static_trips/static_shapes rows on its next reload, or a
        # schedule-revision boundary could never be explained by a real
        # before/after change in planned trips/vehicle-km.
        #
        # trip_count is a plain COUNT of static_trips rows (mirrors
        # trips.txt) — deliberately NOT scaled by calendar_dates service
        # days (unlike pipeline.reports.service_delivered's date-range
        # planned_trips): this table describes the schedule DEFINITION
        # itself, comparable across versions independent of any date range.
        # vehicle_km sums each trip's shape length (via PostGIS geography,
        # so units are correct on a sphere, not planar degrees) once per
        # trip; a trip whose shape_id doesn't resolve in static_shapes is
        # excluded from the SUM (undercounts rather than aborting), and the
        # whole figure is NULL (not 0) when this agency has no shapes loaded
        # for any trip at all — see the migration's own docstring.
        if has_static:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        MAX(t.static_version_id) AS static_version_id,
                        COUNT(*) AS trip_count,
                        COUNT(s.geom) AS trips_with_shape,
                        SUM(CASE WHEN s.geom IS NOT NULL THEN ST_Length(s.geom::geography) END) / 1000.0 AS vehicle_km
                    FROM static_trips t
                    LEFT JOIN static_shapes s
                      ON s.agency_id = t.agency_id AND s.shape_id = t.shape_id
                    WHERE t.agency_id = %(agency_id)s
                    """,
                    p,
                )
                version_row = cur.fetchone()
            static_version_id, trip_count, trips_with_shape, vehicle_km = version_row or (None, 0, 0, None)
            # No backfill: a trip row inserted before migration 0036 (or by an
            # ingest strategy that never sets static_version_id at all) has a
            # NULL static_version_id, and MAX(...) over an all-NULL column is
            # NULL — there is no version key to upsert a row under, so this
            # agency simply gets no row here yet, same "not available until a
            # real value exists" convention as every other nullable column
            # added by item 88.
            if static_version_id is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO agg_static_version_summary "
                        "(agency_id, static_version_id, trip_count, vehicle_km, computed_at) "
                        "VALUES (%s, %s, %s, %s, now()) "
                        "ON CONFLICT (agency_id, static_version_id) DO UPDATE SET "
                        "trip_count = EXCLUDED.trip_count, vehicle_km = EXCLUDED.vehicle_km, "
                        "computed_at = EXCLUDED.computed_at",
                        (agency_id, static_version_id, trip_count, vehicle_km),
                    )
                logger.info(
                    f"  agg_static_version_summary: version={static_version_id} trip_count={trip_count} "
                    f"vehicle_km={vehicle_km} ({trips_with_shape}/{trip_count} trips shaped)"
                )
            else:
                logger.info("  agg_static_version_summary: 0 rows (static_version_id not populated)")
        else:
            logger.info("  agg_static_version_summary: 0 rows (no static schedule loaded)")

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
