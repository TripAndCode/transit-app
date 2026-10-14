"""Seed ``agg_route_daily_dist`` rows the way analyze() builds them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from pipeline.day_types import DayType, fallback_day_type
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    N_BUCKETS,
    bucketize,
)


def hist_of(delays: Sequence[int]) -> list[int]:
    """The delay histogram analyze() stores for ``delays``."""
    hist = [0] * N_BUCKETS
    for d in delays:
        hist[bucketize(d)] += 1
    return hist


async def seed_route_dist_day(conn, agency_id: int, route_code: str, day: date, delays: Sequence[int], service_type=""):
    """One (route, service, date) row of ``agg_route_daily_dist`` over the deduped ``delays``.

    ``conn`` is an asyncpg connection or pool.
    """
    await conn.execute(
        "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, samples, sum_delay_sec, "
        "on_time_count, late5_count, hist) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        agency_id,
        day,
        route_code,
        service_type,
        len(delays),
        sum(delays),
        sum(1 for d in delays if d <= LEGACY_ON_TIME_LATE_TOLERANCE_SEC),
        sum(1 for d in delays if d > LEGACY_SEVERE_LATE_TOLERANCE_SEC),
        hist_of(delays),
    )


def recent_days_of_class(today: date, count: int) -> list[date]:
    """The ``count`` newest dates before ``today`` the weekday rule files with it.

    An agency with no calendar loaded classes its dates by that rule.
    """
    wanted: DayType = fallback_day_type(today)
    days: list[date] = []
    d = today - timedelta(days=1)
    while len(days) < count:
        if fallback_day_type(d) == wanted:
            days.append(d)
        d -= timedelta(days=1)
    return days
