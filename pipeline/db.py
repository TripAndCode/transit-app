"""Shared DB helpers and SQL builders for the (psycopg2) Postgres side of
this codebase, plus the ClickHouse dedup query builder.

`build_dedup_ch_sql` lives here so the analyze materializer, every report
endpoint, and the LLM tool helpers can't drift apart on the definition of
"the latest observation per stop event" — they all read the ClickHouse
`updates` table through this one builder.
"""

import psycopg2

from pipeline.clickhouse import UPDATES_TABLE, checked_table
from pipeline.service_day import service_date_ch

# The database the API and the pipeline CLI connect to when DATABASE_URL is
# unset or empty. The test session vets this same value against the dev ports, so a
# production fallback that bypasses it escapes that guard.
DEFAULT_DATABASE_URL = "postgresql://localhost/transit"

# ISODOW (Mon=1..Sun=7). Aligns with api/range.dow_clause and
# agg_route_hour_dow.dow (SMALLINT).
_DOW_JP_TO_ISO = {"月": 1, "火": 2, "水": 3, "木": 4, "金": 5, "土": 6, "日": 7}
_DOW_ISO_TO_JP = {v: k for k, v in _DOW_JP_TO_ISO.items()}

# Upper bound on a believable departure delay, in seconds (120 min). Observations
# beyond ±this are excluded from the shared dedup (and from the heatmap aggregates
# in pipeline/analyze.py, which import this constant) as data-quality faults.
#
# Why it exists: a realtime TripUpdate feed can freeze and keep re-emitting an
# impossible delay (many hours late) for as long as it stays stuck. Such a
# reading is a stale feed, not a delay, and a handful of them would dominate any
# average over a stop or route.
#
# Clamping here, in the ONE shared dedup builder, protects every averaged surface
# (reports, overview, route-summary, and the live Ask/report queries) — not just
# the heatmap. Raw `updates` is left intact, so feed-health forensics still work
# and the ceiling is re-tunable by re-analyze. Every other hand-rolled dedup on
# the Map tab (api.routers.map's live/route/stop-profile queries,
# pipeline.reports.map's shape-vote/stats queries) applies this same clamp
# directly in its own WHERE, even where it computes no average: a frozen feed
# can report the same implausible reading as a single current value, not just
# skew a mean. The ceiling sits well above any plausible real delay, so a
# genuinely very-late bus still counts everywhere.
MAX_PLAUSIBLE_DELAY_SEC = 7200


def observation_filter_ch(extra_where: str = "") -> str:
    """The row-level WHERE every read of observations shares: the agency, a
    measured `dep_delay` inside the plausibility clamp, and the caller's own
    filter. Columns are `u.`-qualified for the alias rule
    :func:`build_dedup_ch_sql` explains."""
    # Wrap in parens so a fragment containing a top-level OR composes correctly.
    extra = f" AND ({extra_where})" if extra_where else ""
    return (
        "u.dep_delay IS NOT NULL AND u.agency_id = {agency_id:UInt16} "
        f"AND u.dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}{extra}"
    )


def build_dedup_ch_sql(
    *,
    extra_where: str = "",
    include_captured_at: bool = False,
    include_scheduled_sec: bool = False,
    table: str = UPDATES_TABLE,
) -> str:
    """Return the SQL body that picks the latest observation per stop event.

    `table` is whichever observation table holds the day
    (`api.clickhouse.table_holding`); both carry the same columns.

    GTFS-RT feeds publish refining `dep_delay` estimates as the trip nears
    each stop. The latest observation is what passengers actually
    experienced; `MAX(dep_delay)` would bias toward the deepest mid-flight
    overestimate.

    Picks the latest observation per stop event via a single-pass
    `GROUP BY` + `argMax`, not a full sort.
    `ORDER BY captured_at DESC, file_name DESC ... LIMIT 1 BY (...)` keeps
    the same row (the one with the maximal `(captured_at, file_name)` per
    group) but forces ClickHouse to fully SORT the entire filtered row set
    before taking the first row of each group, which at full-history scale
    with no date filter does not complete in a reasonable time.
    `argMax(dep_delay, (captured_at, file_name))` returns the `dep_delay`
    from the row where the tuple `(captured_at, file_name)` is maximal
    within the group — latest observation wins, `file_name` breaks ties —
    computed as a streaming aggregate. File names are unique per poll and
    sort consistently within a day, which is all the tiebreak needs.

    When `include_captured_at`, the winning row's `captured_at` is exactly
    `max(captured_at)` within the group: ties on `captured_at` (broken by
    `file_name`) share the same `captured_at` value by definition, so a
    plain `max()` — not a second `argMax` — is correct and cheaper. The
    output column is aliased `last_captured_at`, NOT `captured_at`: a caller
    combining `include_captured_at=True` with an `extra_where` fragment that
    references bare `captured_at` (e.g. `date_range_clause_ch`/`dow_clause_ch`
    in api/range.py) would otherwise collide with this SELECT-list alias —
    see the `u.`-qualification note below for why ClickHouse's alias
    substitution rule turns that into `ILLEGAL_AGGREGATION` (confirmed
    against a live instance). No current caller combines the two, but the
    distinct alias name means no future one can hit this by accident.

    The `date` column is the *service date* (`pipeline.service_day`): the
    Asia/Tokyo day shifted back by the service-day cutover, so a stop polled
    before and after midnight is one event of one day. It is never a bare
    `toDate(captured_at)`: the ClickHouse column is UTC
    (`DateTime64(0, 'UTC')`), so a UTC day would misbucket every row whose
    captured_at falls in UTC 15:00-23:59 (JST 00:00-08:59 the next day).
    `toDate(value, timezone)` converts to a calendar date in the given
    timezone regardless of the column's stored timezone. The `GROUP BY`
    repeats the full expression rather than the `date` alias, to sidestep any
    ambiguity in how ClickHouse resolves a GROUP BY key that shares a name
    with a SELECT-list alias.

    Takes `agency_id` as a named parameter (`{agency_id:UInt16}`) rather
    than a Python-formatted literal, for clickhouse-connect's server-side
    parameter binding.

    The SELECT list is `route_code, service_type, scheduled_time, trip_id,
    date, stop_sequence, dep_delay[, last_captured_at][, scheduled_sec]`.
    `scheduled_sec`'s position shifts with `include_captured_at`, so a caller
    wanting it reads the result by column name (e.g.
    `pipeline.reports.filters._ch_rows`).

    Departures only: a row with no measured `dep_delay` never reaches the
    `argMax`, and the feeds report a stop's arrival in a poll of its own, so
    this query never sees an arrival. A reader pairing a stop's arrival with
    its departure uses `build_stop_visits_ch_sql`.

    `include_scheduled_sec` projects the raw seconds-since-service-day-start
    schedule value ingest stored alongside `scheduled_time` (see
    `pipeline.strategies._time.parse_departure_time`) — populated for BOTH
    normal and GTFS's after-midnight extended-hour (`hour >= 24`) departures,
    unlike `scheduled_time` itself, which is NULL for the latter (no same-day
    "HH:MM[:SS]" string can hold it). A caller deriving an hour-of-day bucket
    that must not silently drop every extended-hour trip should read
    `scheduled_sec` instead of parsing `scheduled_time`'s first two
    characters. It is the same latest poll's value as `dep_delay`, not any
    poll's: a static reload can move an event's departure between two of its
    polls, and for an extended-hour departure nothing in the `GROUP BY` keeps
    those polls apart, because `scheduled_time` is NULL in every one of them.
    The value is wrapped in a `tuple` so `argMax` keeps a latest poll whose
    `scheduled_sec` is NULL (a reload that leaves the stop with no departure
    time) instead of skipping it for an older poll's schedule, which `argMax`
    does to a NULL argument.

    Every reference to a base-table column that shares its name with a
    SELECT-list alias (`dep_delay`) is qualified with the `u.` table alias
    below.
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
    captured = ", max(u.captured_at) AS last_captured_at" if include_captured_at else ""
    scheduled_sec = (
        ", tupleElement(argMax(tuple(u.scheduled_sec), (u.captured_at, u.file_name)), 1) AS scheduled_sec"
        if include_scheduled_sec
        else ""
    )
    return (
        "SELECT u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        f"{service_date_ch('u.captured_at')} AS date, u.stop_sequence, "
        f"argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay{captured}{scheduled_sec} "
        f"FROM {checked_table(table)} AS u "
        f"WHERE {observation_filter_ch(extra_where)} "
        "GROUP BY u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        f"{service_date_ch('u.captured_at')}, u.stop_sequence"
    )


def _plausible_delay_ch(column: str) -> str:
    """A measured delay inside the ±MAX_PLAUSIBLE_DELAY_SEC clamp."""
    return f"({column} IS NOT NULL AND {column} BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC})"


def build_stop_visits_ch_sql(
    *,
    extra_where: str = "",
    include_scheduled_sec: bool = False,
    table: str = UPDATES_TABLE,
) -> str:
    """Return the SQL body that resolves one row per stop visit (a trip's stop
    on one service day) with the latest plausible departure AND the latest
    plausible arrival the feed reported for it, for the readers that pair the
    two: dwell time and running time (`pipeline.dwell_run`).

    The feeds that send arrivals report a stop's arrival and its departure in
    separate polls: a row carries one or the other, never both, and a trip's
    last stop is reported only as an arrival. `build_dedup_ch_sql` keeps only
    rows with a departure, so it never sees an arrival. This query admits a
    row carrying either and resolves each kind on its own:

    - `dep_delay` is picked exactly as `build_dedup_ch_sql` picks it:
      implausible readings are dropped, then the latest
      `(captured_at, file_name)` wins. NULL for a visit reported only as an
      arrival.
    - `arr_delay` goes through the same clamp and the same latest-wins pick
      over the arrival readings alone. A departure poll's empty arrival field
      is not a retraction, so it never hides an earlier arrival. NULL when the
      visit has no plausible arrival.
    - A visit with no plausible reading of either kind has no row, just as
      the dedup has none for a visit with no plausible departure.

    The visit is keyed on `(trip_id, service date, stop_sequence)` alone.
    `route_code`, `service_type` and `scheduled_time` come from the latest
    departure poll, or the latest arrival poll for an arrival-only visit, each
    wrapped in a tuple so a NULL there is kept rather than skipped by
    `argMax`. Ingest looks all three up by trip and stop, so they agree across
    a visit's polls unless a static reload lands between them. Keying on them
    would then split the arrival from its departure and leave two rows for
    one stop in a running-time window. Wherever they agree, a row with a
    departure is the dedup's row for that visit, column for column.

    `scheduled_sec`, when requested, is the latest departure poll's, as in the
    dedup, and NULL for an arrival-only visit.

    Columns, in this order (`pipeline/analyze.py` loads them by position):
    `route_code, service_type, scheduled_time, trip_id, date, stop_sequence,
    dep_delay[, scheduled_sec], arr_delay`.

    The inner query's aliases are named apart from every `updates` column, so
    a caller's `extra_where` (applied to raw rows, like the dedup's) can name
    `route_code` or `scheduled_time` without ClickHouse substituting an
    aggregate into WHERE (the alias rule `build_dedup_ch_sql`'s docstring
    describes).
    """
    dep_ok = _plausible_delay_ch("u.dep_delay")
    arr_ok = _plausible_delay_ch("u.arr_delay")
    latest = "(u.captured_at, u.file_name)"
    key = "tuple(u.route_code, u.service_type, u.scheduled_time)"
    extra = f" AND ({extra_where})" if extra_where else ""
    scheduled_sec_inner = (
        f", tupleElement(argMaxIf(tuple(u.scheduled_sec), {latest}, {dep_ok}), 1) AS visit_scheduled_sec"
        if include_scheduled_sec
        else ""
    )
    scheduled_sec_outer = ", visit_scheduled_sec AS scheduled_sec" if include_scheduled_sec else ""
    return (
        "SELECT tupleElement(visit_key, 1) AS route_code, tupleElement(visit_key, 2) AS service_type, "
        "tupleElement(visit_key, 3) AS scheduled_time, visit_trip_id AS trip_id, visit_date AS date, "
        f"visit_stop_sequence AS stop_sequence, visit_dep_delay AS dep_delay{scheduled_sec_outer}, "
        "visit_arr_delay AS arr_delay "
        "FROM ("
        f"SELECT u.trip_id AS visit_trip_id, {service_date_ch('u.captured_at')} AS visit_date, "
        "u.stop_sequence AS visit_stop_sequence, "
        f"if(countIf({dep_ok}) > 0, argMaxIf({key}, {latest}, {dep_ok}), argMaxIf({key}, {latest}, {arr_ok})) "
        "AS visit_key, "
        f"argMaxIf(u.dep_delay, {latest}, {dep_ok}) AS visit_dep_delay{scheduled_sec_inner}, "
        f"argMaxIf(u.arr_delay, {latest}, {arr_ok}) AS visit_arr_delay "
        f"FROM {checked_table(table)} AS u "
        f"WHERE u.agency_id = {{agency_id:UInt16}} AND ({dep_ok} OR {arr_ok}){extra} "
        f"GROUP BY u.trip_id, {service_date_ch('u.captured_at')}, u.stop_sequence"
        ")"
    )


def hms_to_sec_sql(column: str) -> str:
    """Return a SQL expression parsing a static-schedule HH:MM:SS (or H:MM)
    text field into seconds since the service day's midnight. Whitespace
    (space, tab, CR, LF) around the value is ignored, because feeds hand-edited
    or exported from spreadsheets can pad a time field.

    Tolerates GTFS's after-midnight extended-hour notation (e.g. "25:30:00")
    since this is plain integer arithmetic, not a cast into a Postgres TIME
    column (which can't represent hour >= 24 -- see
    pipeline/strategies/_time.py's normalize_departure_time, which is why the
    INGEST-time `scheduled_time` column drops such trips instead). A value
    that doesn't match the expected shape (missing/malformed static data)
    resolves to NULL rather than raising and aborting the caller for the
    whole agency -- the same "degrade the one row, don't abort the batch"
    convention `pipeline.reports.rankings.compute_hourly_heatmap`'s live
    ClickHouse fallback already uses for its own
    `toUInt8OrNull(substring(scheduled_time, 1, 2))` hour extraction.

    Lives here (not in `pipeline.analyze`, its original caller) so any
    Postgres-side reader needing a static `arrival_time`/`departure_time` in
    seconds -- not just the `analyze()` batch builders -- can share the exact
    same parse instead of re-deriving it.
    """
    text = f"btrim({column}, E' \\t\\r\\n')"
    return (
        f"CASE WHEN {text} ~ '^[0-9]{{1,3}}:[0-5][0-9](:[0-5][0-9])?$' THEN "
        f"split_part({text}, ':', 1)::int * 3600 "
        f"+ split_part({text}, ':', 2)::int * 60 "
        f"+ COALESCE(NULLIF(split_part({text}, ':', 3), ''), '0')::int "
        f"ELSE NULL END"
    )


def build_prediction_accuracy_ch_sql(*, extra_where: str = "") -> str:
    """Return the SQL body that gathers EVERY raw observation per stop event
    (not just the latest one `build_dedup_ch_sql` resolves to), for
    `pipeline.prediction_accuracy`'s early-vs-final delay-estimate error
    metric.

    Same grouping key, same `agency_id`/plausibility-clamp `WHERE`, and the
    same `argMax(u.dep_delay, (u.captured_at, u.file_name))` final-value
    resolution as `build_dedup_ch_sql` (kept byte-for-byte parallel so the
    two queries can never silently disagree about which row is "the final
    observation" for a stop event) -- but this query ALSO projects
    `groupArray(tuple(u.captured_at, u.dep_delay))`, the raw per-observation
    history `pipeline.prediction_accuracy.compute_stop_event_errors` needs
    to compare early readings against that final value.

    `u.scheduled_time IS NOT NULL` drops stop events with no scheduled
    instant to measure lead time from -- an extended-hour or unmatched row is
    stored with a NULL `scheduled_time` -- so `pipeline.prediction_accuracy`
    never has to parse one.

    `HAVING count() > 1` drops every stop event observed exactly once: a
    singleton has no EARLY observation to compare against its own (only)
    final one, so it can never contribute to this metric and would just be
    dead weight in the result set (and in `groupArray`'s per-row memory).

    Column order/count in the SELECT list is exactly `route_code,
    service_type, scheduled_time, trip_id, date, stop_sequence,
    final_dep_delay, observations` -- `pipeline.prediction_accuracy.
    rows_to_lead_bucket_stats` consumes the result by tuple position.

    See `build_dedup_ch_sql`'s own docstring for the rationale behind the
    JST `toDate()` bucketing, the `{agency_id:UInt16}` parameter binding,
    and qualifying every base-table column reference with the `u.` alias
    (all identical here, not repeated).
    """
    extra = f" AND ({extra_where})" if extra_where else ""
    return (
        "SELECT u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        f"{service_date_ch('u.captured_at')} AS date, u.stop_sequence, "
        "argMax(u.dep_delay, (u.captured_at, u.file_name)) AS final_dep_delay, "
        "groupArray(tuple(u.captured_at, u.dep_delay)) AS observations "
        "FROM updates AS u "
        "WHERE u.dep_delay IS NOT NULL AND u.scheduled_time IS NOT NULL "
        "AND u.agency_id = {agency_id:UInt16} "
        f"AND u.dep_delay BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}{extra} "
        "GROUP BY u.route_code, u.service_type, u.scheduled_time, u.trip_id, "
        f"{service_date_ch('u.captured_at')}, u.stop_sequence "
        "HAVING count() > 1"
    )


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
