"""The SQL analyze runs for the builders written inline in `_analyze_locked`.

Every function returns exactly the statement analyze executes, with module
constants interpolated and only the run-varying date filter passed in, so the
text can be rendered and compared without a database.

ClickHouse statements keep their `{name:Type}` parameters for the server; they
are concatenated rather than f-strings so those braces never need escaping.
"""

from pipeline.service_day import service_date_ch, service_day_offset_ch

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
# the schedule plus dep_delay) -- cardinality is bounded by routes ×
# stops × days, not by raw observation count, so it's safe to fetch
# as one block (ch_client.query(), not the streaming reader
# `_analyze_visits` needs) even though the pre-aggregation GROUP BY
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
    """Per (route, stop, service date), the array of actual event times."""
    return (
        """
                WITH per_event AS (
                    SELECT route_code, stop_id, """
        + _SERVICE_DATE
        + """ AS svc_date, trip_id,
                           stop_sequence,
                           argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay,
                           argMax(u.scheduled_sec, (u.captured_at, u.file_name)) AS scheduled_sec
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
        + """) + toInt64(dep_delay)) AS times
                FROM per_event
                GROUP BY route_code, stop_id, svc_date
                """
    )
