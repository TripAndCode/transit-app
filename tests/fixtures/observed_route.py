"""Seed a route's daily aggregates the way analyze() leaves them."""

from __future__ import annotations

from datetime import date, timedelta

from tests.fixtures.route_dist import seed_route_dist_day


async def seed_observed_route(conn, agency_id: int, route_code: str, first: str, last: str, delay: int = 400):
    """A route seen every day from ``first`` to ``last`` (ISO dates), 25
    departures a day each ``delay`` seconds late, in every daily table a
    route list or the retired and zero-delay checks read.

    ``conn`` is an asyncpg connection or pool.
    """
    day = date.fromisoformat(first)
    while day <= date.fromisoformat(last):
        await conn.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, "
            "worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
            "VALUES ($1, $2, $3, '', $4, $4, 1, 25, NOW(), $5)",
            agency_id,
            day,
            route_code,
            delay,
            delay * 25,
        )
        await conn.execute(
            "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples, "
            "sum_delay_sec, sum_late_sec) VALUES ($1, $2, $3, '', $4, 25, $5, $6)",
            agency_id,
            day,
            route_code,
            delay / 60,
            delay * 25,
            max(delay, 0) * 25,
        )
        await seed_route_dist_day(conn, agency_id, route_code, day, [delay] * 25)
        day += timedelta(days=1)
