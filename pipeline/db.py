"""Shared DB helpers and SQL builders used by both psycopg2 (analyze) and
asyncpg (api / reports / tool_queries) paths.

The dedup SQL lives here so the two paths never drift apart. Update one
place and every report endpoint, the analyze materializer, and the LLM
tool helpers all pick up the change atomically.
"""

import psycopg2

# ISODOW (Mon=1..Sun=7). Aligns with api/range.dow_clause and agg_route_dow.dow
# (SMALLINT) after migration 0011.
_DOW_JP_TO_ISO = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 7}
_DOW_ISO_TO_JP = {v: k for k, v in _DOW_JP_TO_ISO.items()}

# Upper bound on a believable departure delay, in seconds (120 min). Observations
# beyond ±this are excluded from the shared dedup (and from the heatmap aggregates
# in pipeline/analyze.py, which import this constant) as data-quality faults.
#
# WHY THIS EXISTS — root-cause from a 2026-06-07 investigation:
#   The map showed 馬木料金所前 (an expressway tollgate stop on route 550332996)
#   at a 72.2-min AVERAGE delay. That stop is actually fine: median 3 min, p90
#   4 min. The mean was hijacked by two trips on 2026-06-07 whose realtime
#   TripUpdate feed FROZE and kept re-emitting an impossible delay — 976 min
#   (16.3 h) and 715 min (11.9 h) — once every ~30 s for minutes. No city bus is
#   16 h late: that magnitude is a stale/stuck feed, not a delay.
#
# Clamping here, in the ONE shared dedup builder, protects every averaged surface
# (reports, overview, route-summary, and the live Ask/report queries) — not just
# the heatmap. Raw `updates` is left intact, so feed-health forensics still work
# and the ceiling is re-tunable by re-analyze. `delays/live` reads raw `updates`
# directly and is intentionally NOT clamped: it's a current-state snapshot, not an
# average. The ceiling sits well above any plausible real delay, so a genuinely
# very-late bus still counts.
MAX_PLAUSIBLE_DELAY_SEC = 7200


def build_dedup_inner_sql(
    *,
    placeholder: str = "%(agency_id)s",
    extra_where: str = "",
    include_captured_at: bool = False,
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
    (reports.py, pipeline.query.tool_queries). `extra_where` is ANDed
    onto the inner WHERE; pass server-built SQL only.

    `include_captured_at` adds the raw `captured_at` to the projection (the
    DISTINCT ON already keeps the latest row per group, so it's that row's
    timestamp) — used by agg_route_daily for a per-day `last_seen_at`.

    The trailing `id DESC` makes the dedup deterministic when two rows
    share the same `captured_at` (different files, same poll second).
    """
    # Wrap in parens so a fragment containing top-level OR composes correctly.
    extra = f" AND ({extra_where})" if extra_where else ""
    captured = ", captured_at" if include_captured_at else ""
    return (
        "SELECT DISTINCT ON (route_code, service_type, scheduled_time, "
        "trip_id, captured_at::date, stop_sequence) "
        "route_code, service_type, scheduled_time, trip_id, "
        f"captured_at::date AS date, stop_sequence, dep_delay{captured} "
        "FROM updates "
        f"WHERE dep_delay IS NOT NULL AND agency_id = {placeholder} "
        f"AND dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}{extra} "
        "ORDER BY route_code, service_type, scheduled_time, trip_id, "
        "captured_at::date, stop_sequence, captured_at DESC, id DESC"
    )


def build_dedup_ch_sql(
    *,
    extra_where: str = "",
    include_captured_at: bool = False,
) -> str:
    """ClickHouse-dialect equivalent of build_dedup_inner_sql.

    Picks the latest observation per stop event via a single-pass
    `GROUP BY` + `argMax`, not a full sort. An earlier version used
    `ORDER BY captured_at DESC, file_name DESC ... LIMIT 1 BY (...)`,
    which is semantically identical (both keep the row with the maximal
    `(captured_at, file_name)` per group) but forces ClickHouse to fully
    SORT the entire filtered row set before taking the first row of each
    group. At agency-8 scale (336M rows, full history, no date filter —
    what `analyze()` runs) that sort didn't complete inside a 300s budget.
    `argMax(dep_delay, (captured_at, file_name))` returns the `dep_delay`
    from the row where the tuple `(captured_at, file_name)` is maximal
    within the group — same latest-observation-wins, `file_name DESC`
    tiebreak semantics as the old ORDER BY, computed as a streaming
    aggregate instead. `id DESC` (a Postgres surrogate key, not carried
    into the ClickHouse schema) is what `file_name DESC` already replaced
    here — file names are unique per poll and sort consistently within a
    day, which is all the original tiebreak needed.

    When `include_captured_at`, the winning row's `captured_at` is exactly
    `max(captured_at)` within the group: ties on `captured_at` (broken by
    `file_name`) share the same `captured_at` value by definition, so a
    plain `max()` — not a second `argMax` — is correct and cheaper.

    `toDate(captured_at, 'Asia/Tokyo')`, NOT bare `toDate(captured_at)`:
    every Postgres connection that ever touched `updates` pins
    `SET TIME ZONE 'Asia/Tokyo'` (see api/main.py::_init_connection's
    docstring), so `captured_at::date` throughout this codebase has
    always meant the JST calendar day, not the UTC one. The ClickHouse
    column is UTC (`DateTime64(0, 'UTC')`); using bare `toDate()` here
    would silently misbucket every row whose captured_at falls in
    UTC 15:00-23:59 (JST 00:00-08:59 the next day) — reproducing the
    exact UTC/JST aggregate-mis-bucketing bug this project already hit
    once (see the design doc's Risks section). `toDate(value, timezone)`
    is a real ClickHouse signature — it converts to a calendar date in
    the given timezone regardless of the column's stored timezone. The
    `GROUP BY` repeats the full `toDate(...)` expression rather than the
    `date` alias, to sidestep any ambiguity in how ClickHouse resolves a
    GROUP BY key that shares a name with a SELECT-list alias.

    Takes `agency_id` as a named parameter (`{agency_id:UInt16}`) rather
    than a Python-formatted literal, for clickhouse-connect's server-side
    parameter binding.

    Column order/count in the SELECT list must stay exactly
    `route_code, service_type, scheduled_time, trip_id, date,
    stop_sequence, dep_delay[, captured_at]` — `pipeline/analyze.py`
    consumes the result by tuple position (`r[-1]` for `captured_at`).

    Every reference to a base-table column that shares its name with a
    SELECT-list alias (`dep_delay`, and `captured_at` when
    `include_captured_at`) is qualified with the `u.` table alias below.
    ClickHouse resolves a *bare* identifier that matches a SELECT alias by
    textually substituting the alias's defining expression wherever that
    bare identifier appears in the query — including back into this
    query's own pre-aggregation `WHERE`, not just a caller's outer clause
    — which turns e.g. `WHERE dep_delay IS NOT NULL` into
    `WHERE argMax(dep_delay, ...) IS NOT NULL` and raises
    `ILLEGAL_AGGREGATION` ("Aggregate function ... is found in WHERE").
    Qualifying with `u.` makes these compound identifiers, which that
    substitution rule doesn't match, so the WHERE clause still sees the
    plain row-level column. Confirmed against a live ClickHouse instance.
    """
    extra = f" AND ({extra_where})" if extra_where else ""
    captured = ", max(u.captured_at) AS captured_at" if include_captured_at else ""
    return (
        "SELECT u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        "toDate(u.captured_at, 'Asia/Tokyo') AS date, u.stop_sequence, "
        f"argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay{captured} "
        "FROM updates AS u "
        "WHERE u.dep_delay IS NOT NULL AND u.agency_id = {agency_id:UInt16} "
        f"AND u.dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}{extra} "
        "GROUP BY u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        "toDate(u.captured_at, 'Asia/Tokyo'), u.stop_sequence"
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
