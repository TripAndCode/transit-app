"""ClickHouse SQL for the workbench. Delay facts come only through
build_dedup_ch_sql, so a run's delays are what the app's reports read: the
latest observation per stop event, with implausible delays clamped out."""

from __future__ import annotations

from pipeline.db import build_dedup_ch_sql
from pipeline.service_day import SERVICE_DAY_CUTOVER_HOURS, service_date_ch, service_day_offset_ch

# Every stop event's rows share one service date (the dedup groups by it), so a
# date range splits the work with no group cut in two. `u.` because the dedup
# query's own SELECT aliases would otherwise capture a bare column name.
_DATE_RANGE = f"{service_date_ch('u.captured_at')} BETWEEN {{start:Date}} AND {{end:Date}}"


# The hour of the schedule string of a run's first stop, for feeds that store no
# scheduled_sec. A clock before the cutover is an after-midnight hour of the
# service day, the 24 or more that _SERVICE_DAY_SEC gives.
_CLOCK_HOUR = "toUInt8OrNull(substring(argMin(scheduled_time, stop_sequence), 1, 2))"

# scheduled_sec on the service day's axis. Ingest stores a feed's clock as
# written, so an un-extended "00:10:00" is 600 and goes through the cutover
# here, while GTFS's extended "24:10:00" is already 87000.
_SERVICE_DAY_SEC = service_day_offset_ch("scheduled_sec")


def runs_sql() -> str:
    """One row per trip run (trip × service date): the mean of its stops'
    departure delays in minutes, and the hour of its first observed stop's
    scheduled departure. The hour comes from scheduled_sec through the
    service-day cutover, so a run after midnight on its service day keeps an
    hour of 24 or more whether its feed writes "00:10:00" or "24:10:00", and
    its span runs forward across midnight. A strategy that
    stores only the "HH:MM" schedule string gets that string's hour instead,
    as the app's own reports read it. A stop with neither field (a non-timepoint
    stop) stays out of its run's mean, and a run left with no hour has no row.
    It also carries the run's service (a timetable service id, or the feed's
    own weekday/holiday label), its count of observed stops, and its scheduled
    span in minutes. Stops and span are read only as history; a row's own
    service doubles as a lookup key fixed by the timetable in advance, the same
    as its route and trip."""
    dedup = build_dedup_ch_sql(include_scheduled_sec=True, extra_where=_DATE_RANGE)
    return f"""
SELECT
    route_code,
    trip_id,
    date AS service_date,
    coalesce(
        intDiv(argMin({_SERVICE_DAY_SEC}, stop_sequence), 3600),
        {_CLOCK_HOUR} + if({_CLOCK_HOUR} < {SERVICE_DAY_CUTOVER_HOURS}, 24, 0)
    ) AS hour,
    avg(dep_delay) / 60 AS mean_delay_min,
    any(service_type) AS service,
    count() AS stops,
    (max({_SERVICE_DAY_SEC}) - min({_SERVICE_DAY_SEC})) / 60 AS span_min
FROM ({dedup})
WHERE route_code IS NOT NULL AND (scheduled_sec IS NOT NULL OR scheduled_time IS NOT NULL)
GROUP BY route_code, trip_id, date
HAVING hour IS NOT NULL
"""


def cells_sql() -> str:
    return f"""
SELECT route_code, service_date, hour, count() AS runs, sum(mean_delay_min) AS delay_sum_min
FROM ({runs_sql()})
GROUP BY route_code, service_date, hour
ORDER BY service_date, route_code, hour
"""
