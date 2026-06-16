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
