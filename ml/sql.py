"""ClickHouse SQL for the workbench. Delay facts come only through
build_dedup_ch_sql, so a run's delays are what the app's reports read: the
latest observation per stop event, with implausible delays clamped out."""

from __future__ import annotations

from pipeline.db import build_dedup_ch_sql

# Every stop event's rows share one JST date (the dedup groups by it), so a
# date range splits the work with no group cut in two. `u.` because the dedup
# query's own SELECT aliases would otherwise capture a bare column name.
_DATE_RANGE = "toDate(u.captured_at, 'Asia/Tokyo') BETWEEN {start:Date} AND {end:Date}"


def runs_sql() -> str:
    """One row per trip run (trip × JST capture date): the mean of its stops'
    departure delays in minutes, and the hour of its first observed stop's
    scheduled departure. The hour comes from scheduled_sec, so a run after
    midnight on its service day keeps an hour of 24 or more. A strategy that
    stores only the "HH:MM" schedule string gets that string's hour instead,
    as the app's own reports read it; a run with neither has no hour and no row."""
    dedup = build_dedup_ch_sql(include_scheduled_sec=True, extra_where=_DATE_RANGE)
    return f"""
SELECT
    route_code,
    trip_id,
    date AS service_date,
    coalesce(
        intDiv(argMin(scheduled_sec, stop_sequence), 3600),
        toUInt8OrNull(substring(argMin(scheduled_time, stop_sequence), 1, 2))
    ) AS hour,
    avg(dep_delay) / 60 AS mean_delay_min
FROM ({dedup})
WHERE route_code IS NOT NULL
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
