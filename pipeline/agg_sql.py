"""The SQL analyze runs for the builders written inline in `_analyze_locked`.

Every function returns exactly the statement analyze executes, with module
constants interpolated and only the run-varying date filter passed in, so the
text can be rendered and compared without a database.

ClickHouse statements keep their `{name:Type}` parameters for the server; they
are concatenated rather than f-strings so those braces never need escaping.

A change to what any of these statements produces is a builder change: bump
the table's version in `pipeline/agg_registry.py` in the same commit.
"""

from pipeline.db import hms_to_sec_sql
from pipeline.dwell_run import DWELL_HI, DWELL_LO, DWELL_WIDTH, RUN_HI, RUN_LO, RUN_WIDTH
from pipeline.headways import HIGH_FREQUENCY_HEADWAY_SEC, dominant_service_ctes
from pipeline.histogram import bucket_case_sql, hist_array_sql
from pipeline.service_day import service_date_ch, service_day_offset_ch, service_day_seconds_sql

_SERVICE_DATE = service_date_ch()


def feed_health_ch_sql(date_where: str) -> str:
    """Per service date: rows carrying a dep_delay, how many of those exceed the
    plausibility clamp, and every row of the date, which is the ledger the next
    run's rebuild decision reads."""
    return (
        """
            SELECT """
        + _SERVICE_DATE
        + """ AS date,
                   countIf(dep_delay IS NOT NULL) AS raw_samples,
                   countIf(abs(dep_delay) > {max_delay:Int32}) AS clamp_count,
                   count() AS total_rows
            FROM updates
            WHERE agency_id = {agency_id:UInt16}
            """
        + date_where
        + """
            GROUP BY date
            """
    )


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
# sentinel outside the real value range (GTFS-RT's
# ScheduleRelationship enums are non-negative) so argMax always sees a real value
# for every row and genuinely reflects the latest observation,
# including a correction back to "not skipped/canceled". Applied
# to both subqueries for the same reason, even though today's
# confirmed-populating feeds always send schedule_relationship_
# trip on every observation (never NULL) -- this is defensive
# symmetry, not dead code, since nothing prevents a future feed
# from sending it more sparingly. Date-scoped like every
# incremental table: both subqueries carry date_where, so a changed
# day is recomputed from all of that day's rows and no other day is
# touched. A day with zero non-executed trips has no matching row
# in the inner UNION, so it emits no row here at all -- the read
# path sums with a zero default rather than assuming row-per-day
# density.
def service_delivered_ch_sql(date_where: str) -> str:
    """Per service date, the trips the feed reported as not run: CANCELED trips and trips with a SKIPPED stop."""
    return (
        """
                SELECT svc_date, count() FROM (
                    SELECT svc_date, trip_id FROM (
                        SELECT """
        + _SERVICE_DATE
        + """ AS svc_date, trip_id,
                               argMax(coalesce(schedule_relationship_trip, -1), (captured_at, file_name)) AS trip_rel
                        FROM updates WHERE agency_id = {agency_id:UInt16}"""
        + date_where
        + """
                        GROUP BY svc_date, trip_id
                    ) WHERE trip_rel = 3
                    UNION DISTINCT
                    SELECT svc_date, trip_id FROM (
                        SELECT """
        + _SERVICE_DATE
        + """ AS svc_date, trip_id,
                               argMax(coalesce(schedule_relationship_stop, -1), (captured_at, file_name)) AS stop_rel
                        FROM updates WHERE agency_id = {agency_id:UInt16}"""
        + date_where
        + """
                        GROUP BY svc_date, trip_id, stop_sequence
                    ) WHERE stop_rel = 1
                ) GROUP BY svc_date
                """
    )


# `argMax(version, cnt)` picks the version stamped on the MOST rows
# that day (mode), not the version of the latest single observation
# -- a reload happening mid-day should attribute that day to whichever
# version actually served most of it, not to whichever the last poll
# happened to see.
def schedule_revision_ch_sql(date_where: str) -> str:
    """Per service date, the timetable version stamped on the most rows."""
    return (
        """
            SELECT svc_date, argMax(version, cnt) AS static_version_id
            FROM (
                SELECT """
        + _SERVICE_DATE
        + """ AS svc_date,
                       static_version_id AS version,
                       count() AS cnt
                FROM updates WHERE agency_id = {agency_id:UInt16}"""
        + date_where
        + """
                GROUP BY svc_date, version
            )
            GROUP BY svc_date
            HAVING static_version_id IS NOT NULL
            """
    )


# One row per (route_code, stop_id, service day) with an ARRAY of
# that group's actual event times (seconds into the service day:
# the schedule plus dep_delay). The pre-aggregation GROUP BY
# underneath it scans every `updates` row of the dates being rebuilt
# (the agency's whole history on a full rebuild).
#
# The per-event dedup is build_dedup_ch_sql's: implausible and NULL
# delays are dropped BEFORE the latest-observation-wins argMax, so a
# frozen-feed spike in an event's latest poll leaves its last
# plausible estimate rather than removing the event and merging its
# two neighbouring gaps. stop_sequence stays in the per-event key
# (as in build_dedup_ch_sql and agg_service_delivered_daily's own
# stop-level subquery) so a route that revisits the same physical
# stop_id within one trip keeps both visits as distinct events; only
# the outer SELECT pools across stop_sequence and trips.
#
# scheduled_sec, not scheduled_time, is the departure: static_join
# writes both, but leaves scheduled_time NULL for an extended-hour
# GTFS departure (24:30:00 is 88200 here), which the scheduled side
# counts through hms_to_sec_sql. It comes from the same latest poll
# as dep_delay: a static reload can move an event's schedule between
# polls, and nothing in this key separates the two. Ingest stores
# the clock as the feed wrote it, so the outer SELECT places it on
# the service day (service_day_offset_ch) before adding the delay:
# a plain "00:10:00" (600) then follows 23:50 as GTFS's
# "24:10:00" (87000) does, instead of sorting before the morning.
# The filters are on `u.` columns
# because an unqualified name matching a SELECT alias in WHERE is
# replaced by that alias's aggregate (see build_dedup_ch_sql).
def route_headway_daily_ch_sql(date_where: str) -> str:
    """Per (route, stop, service date), the array of actual event times and of
    the version names they were ingested against ('' for none), each event's
    from its latest poll like its delay."""
    return (
        """
                WITH per_event AS (
                    SELECT route_code, stop_id, """
        + _SERVICE_DATE
        + """ AS svc_date, trip_id,
                           stop_sequence,
                           argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay,
                           argMax(u.scheduled_sec, (u.captured_at, u.file_name)) AS scheduled_sec,
                           argMax(ifNull(u.static_version_id, ''), (u.captured_at, u.file_name)) AS stem
                    FROM updates AS u
                    WHERE u.agency_id = {agency_id:UInt16}
                      AND u.stop_id IS NOT NULL AND u.route_code IS NOT NULL
                      AND u.scheduled_sec IS NOT NULL
                      AND u.dep_delay IS NOT NULL
                      AND u.dep_delay BETWEEN {min_delay:Int32} AND {max_delay:Int32}"""
        + date_where
        + """
                    GROUP BY route_code, stop_id, svc_date, trip_id, stop_sequence
                )
                SELECT route_code, stop_id, svc_date,
                       groupArray(toInt64("""
        + service_day_offset_ch("scheduled_sec")
        + """) + toInt64(dep_delay)) AS times,
                       groupArray(stem) AS stems
                FROM per_event
                GROUP BY route_code, stop_id, svc_date
                """
    )


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
# unvalidated, so it is parsed with the shared hms_to_sec_sql
# (deliberately NOT capped at 24h -- GTFS's
# post-midnight-continuation hours like "25:30:00" are valid
# schedule data and must not raise or misparse; a malformed shape
# parses to NULL and is excluded, exactly as in every other reader
# of static times). The parsed value is then placed on the service
# day (service_day_seconds_sql): a feed that writes an
# after-midnight departure as a plain clock ("00:10:00") would
# otherwise sort it before the morning's first departure, where
# GTFS's "24:10:00" sorts after 23:50.
def route_headway_sql(all_routes: bool = False) -> str:
    """Per route, the scheduled headway median, sample count, high-frequency flag and mean wait.

    *all_routes* selects instead every route of a dominant service with its
    median, NULL where the timetable gives none: what pipeline.static_headways
    keeps per timetable.
    """
    hf_thr = HIGH_FREQUENCY_HEADWAY_SEC
    if all_routes:
        selected = """
                SELECT ds.route_code, m.scheduled_headway_median_sec
                FROM dominant_service ds LEFT JOIN medians m USING (route_code)
            """
    else:
        selected = f"""
                SELECT %(agency_id)s AS agency_id, route_code,
                       scheduled_headway_median_sec, scheduled_samples,
                       scheduled_headway_median_sec <= {hf_thr} AS is_high_frequency,
                       scheduled_wait_mean_sec
                FROM medians
            """
    return f"""
                WITH {dominant_service_ctes("%(agency_id)s")},
                scheduled_departures AS (
                    SELECT twr.route_code, sst.stop_id,
                        {service_day_seconds_sql(hms_to_sec_sql("sst.departure_time"))} AS dep_sec
                    FROM trips_with_route twr
                    JOIN dominant_service ds
                      ON ds.route_code = twr.route_code AND ds.service_id = twr.service_id
                    JOIN static_stop_times sst
                      ON sst.agency_id = %(agency_id)s AND sst.trip_id = twr.trip_id
                ),
                gaps AS (
                    SELECT route_code,
                           dep_sec - LAG(dep_sec) OVER (PARTITION BY route_code, stop_id ORDER BY dep_sec)
                               AS headway_sec
                    FROM scheduled_departures
                    WHERE dep_sec IS NOT NULL
                ),
                medians AS (
                    SELECT route_code,
                           PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY headway_sec) AS scheduled_headway_median_sec,
                           COUNT(*) AS scheduled_samples,
                           -- E[H^2] / (2*E[H]) over this route's scheduled headway
                           -- distribution -- the mean-wait-time formula
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
                ){selected}"""


# Decomposes arr_delay + dep_delay into per-stop-visit dwell time (this
# visit's departure minus its own arrival) and running time (this
# visit's arrival minus the PREVIOUS visit's departure) -- see
# pipeline/dwell_run.py for the shared math this mirrors in SQL. Reads
# the stop visits _analyze_locked materialises once into _analyze_visits
# before this runs: one row per trip, service day and stop, pairing the arrival and the
# departure the feed reports in separate polls, with the scheduled times of
# the archived timetable the visit's version name maps to (the loaded one
# when it maps to none). A visit reported only as an arrival (every trip's
# terminus) has a NULL dep_delay; one reported only as a departure, a NULL
# arr_delay.
def dwell_run_sql() -> str:
    """Per (date, route, service), dwell and running-time sample counts, sums and histograms."""
    dwell_bucket_expr = bucket_case_sql("dwell_sec", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
    run_bucket_expr = bucket_case_sql("running_sec", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
    dwell_hist_expr = hist_array_sql("bd", lo=DWELL_LO, hi=DWELL_HI, width=DWELL_WIDTH)
    run_hist_expr = hist_array_sql("br", lo=RUN_LO, hi=RUN_HI, width=RUN_WIDTH)
    # On the service day's axis, so a run from 23:58 to a stop the
    # timetable writes as "00:03:00" is five minutes forward, as it is
    # to GTFS's "24:03:00".
    sched_arr_expr = service_day_seconds_sql(hms_to_sec_sql("d.sched_arrival_time"))
    sched_dep_expr = service_day_seconds_sql(hms_to_sec_sql("d.sched_departure_time"))
    return f"""
                WITH visits AS (
                    SELECT
                        d.route_code, COALESCE(d.service_type, '') AS service_type,
                        d.trip_id, d.date, d.stop_sequence, d.dep_delay, d.arr_delay, d.dep_observed,
                        {sched_arr_expr} AS sched_arr_sec,
                        {sched_dep_expr} AS sched_dep_sec
                    FROM _analyze_visits d
                ),
                actuals AS (
                    -- A stop with no archived stop time (the visits scan's
                    -- archive join found none) or lacking arrival_time/departure_time within one
                    -- yields NULL sched_dep_sec/sched_arr_sec here -- every
                    -- such visit is still KEPT (not filtered out) so the
                    -- LAG() window below sees every stop_sequence in order;
                    -- dropping the row would let LAG() silently skip past it
                    -- and pair the FOLLOWING stop with the wrong previous
                    -- departure. Each actual time needs its own delay and its
                    -- own scheduled time, so an arrival-only visit (no
                    -- dep_delay) runs into this stop but leaves the next
                    -- stop's running time unknown -- the split
                    -- pipeline.dwell_run's module docstring describes.
                    SELECT route_code, service_type, trip_id, date, stop_sequence, dep_observed,
                        CASE WHEN dep_delay IS NOT NULL AND sched_dep_sec IS NOT NULL
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
                        -- Dwell takes only a departure the feed reported
                        -- after the arrival (dep_observed): one still
                        -- standing from before it is a prediction, not a
                        -- departure anyone saw. Running time takes the
                        -- previous departure either way.
                        CASE WHEN actual_arr_sec IS NOT NULL AND dep_observed
                             THEN actual_dep_sec - actual_arr_sec END AS dwell_sec,
                        CASE WHEN actual_arr_sec IS NOT NULL AND prev_actual_dep_sec IS NOT NULL
                             THEN actual_arr_sec - prev_actual_dep_sec END AS running_sec
                    FROM with_prev
                    -- Filtered here, after LAG(), not in `visits`: a no-route
                    -- visit still anchors the next stop's running time, but
                    -- cannot enter the NOT NULL route_code key.
                    WHERE route_code IS NOT NULL
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


STATIC_VERSION_SUMMARY_SQL = """
                    WITH shape_m AS MATERIALIZED (
                        SELECT shape_id, ST_Length(geom::geography) AS m
                        FROM static_shapes
                        WHERE agency_id = %(agency_id)s AND geom IS NOT NULL
                    )
                    SELECT
                        MAX(t.static_version_id) AS static_version_id,
                        COUNT(*) AS trip_count,
                        COUNT(s.m) AS trips_with_shape,
                        SUM(s.m) / 1000.0 AS vehicle_km
                    FROM static_trips t
                    LEFT JOIN shape_m s ON s.shape_id = t.shape_id
                    WHERE t.agency_id = %(agency_id)s
                    """

STATIC_VERSION_SUMMARY_UPSERT_SQL = (
    "INSERT INTO agg_static_version_summary "
    "(agency_id, static_version_id, trip_count, vehicle_km, computed_at) "
    "VALUES (%s, %s, %s, %s, now()) "
    "ON CONFLICT (agency_id, static_version_id) DO UPDATE SET "
    "trip_count = EXCLUDED.trip_count, vehicle_km = EXCLUDED.vehicle_km, "
    "computed_at = EXCLUDED.computed_at"
)


# ClickHouse sorts each stop's routes by bytes. The Postgres copy keeps the
# order string_agg(... ORDER BY route_code) gave: the database collation's.
# A list with no comma has nothing to order, and string_to_array splits ''
# into no element at all, which would write NULL. Splitting on commas is
# sound because a route code holds none: the API's `routes` filter splits on them too.
STOP_ROUTES_ORDER_SQL = (
    "UPDATE agg_stop_routes SET route_codes = "
    "(SELECT string_agg(r, ',' ORDER BY r) FROM unnest(string_to_array(route_codes, ',')) AS r) "
    "WHERE agency_id = %s AND strpos(route_codes, ',') > 0"
)
