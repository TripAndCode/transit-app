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
- agg_route_daily_dwell_run — per-day dwell/running-time distribution (decomposition view; static_join agencies only)
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
from datetime import timezone

import psycopg2.extras

from api.range import time_band_case_sql
from pipeline.clickhouse import max_captured_at as ch_max_captured_at
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, _static_loaded, build_dedup_ch_sql
from pipeline.dwell_run import DWELL_HI, DWELL_LO, DWELL_WIDTH, RUN_HI, RUN_LO, RUN_WIDTH
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
    "agg_route_daily_dwell_run",
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


def _hms_to_sec_sql(column: str) -> str:
    """Return a SQL expression parsing a static-schedule HH:MM:SS (or H:MM)
    text field into seconds since the service day's midnight.

    Tolerates GTFS's after-midnight extended-hour notation (e.g. "25:30:00")
    since this is plain integer arithmetic, not a cast into a Postgres TIME
    column (which can't represent hour >= 24 -- see
    pipeline/strategies/_time.py's normalize_departure_time, which is why the
    INGEST-time `scheduled_time` column drops such trips instead). A value
    that doesn't match the expected shape (missing/malformed static data)
    resolves to NULL rather than raising and aborting analyze() for the
    whole agency -- the same "degrade the one row, don't abort the batch"
    convention compute_hourly_heatmap's live ClickHouse fallback already
    uses for its own `toUInt8OrNull(substring(scheduled_time, 1, 2))` hour
    extraction.
    """
    return (
        f"CASE WHEN {column} ~ '^[0-9]{{1,3}}:[0-9]{{2}}(:[0-9]{{2}})?$' THEN "
        f"split_part({column}, ':', 1)::int * 3600 "
        f"+ split_part({column}, ':', 2)::int * 60 "
        f"+ COALESCE(NULLIF(split_part({column}, ':', 3), ''), '0')::int "
        f"ELSE NULL END"
    )


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
        # Materialization here is gated on ingest_strategy alone, not on
        # pipeline.strategies.static_join.RT_FIELD_COVERAGE_CONFIRMED_AGENCIES
        # -- sharing an ingest strategy does not by itself prove a given agency's
        # feed actually populates these fields. Today this is safe because every
        # reader of this table (pipeline.reports.service_delivered) re-applies
        # that confirmed-agency intersection before returning data; any new
        # direct reader of agg_service_delivered_daily must do the same or it
        # will treat an unconfirmed agency's rows as trustworthy.
        with conn.cursor() as cur:
            cur.execute("SELECT ingest_strategy FROM agencies WHERE agency_id = %s", (agency_id,))
            row = cur.fetchone()
        if row and row[0] == "static_join":
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
        # intersects against RT_FIELD_COVERAGE_CONFIRMED_AGENCIES before
        # trusting these rows.
        if has_static and row and row[0] == "static_join":
            dwell_bucket_expr = bucket_case_sql("dwell_sec", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
            run_bucket_expr = bucket_case_sql("running_sec", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
            dwell_hist_expr = hist_array_sql("bd", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
            run_hist_expr = hist_array_sql("br", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
            sched_arr_expr = _hms_to_sec_sql("sst.arrival_time")
            sched_dep_expr = _hms_to_sec_sql("sst.departure_time")
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
