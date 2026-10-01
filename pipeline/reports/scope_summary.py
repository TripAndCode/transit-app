"""The data behind the scope controls' visuals.

Every section reads ``agg_route_daily_dist`` alone: it has a true DATE
column for a sargable range scan and a 37-bin delay histogram, so the
controls never wait on a live ClickHouse scan. The aggregate has no hour of
day, stop or direction, so ``time_band``, ``hour``, ``stop`` and ``dir`` are
not applied here; the endpoint's ``scope_applied`` says so.

Each section drops the one condition it exists to choose:

- ``days``: the last :data:`DAYS_WINDOW` days of data, ignoring the period
  and the weekday filter, so the brush can move the period anywhere.
- ``weekdays``: the selected period, ignoring the weekday filter.
- ``routes``: the selected period, ignoring the routes filter.
- ``tolerance``: the whole scope, as the on-time share at each late
  tolerance.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from api.range import RangeCtx
from pipeline.histogram import N_BUCKETS, count_in_range
from pipeline.reports.filters import _dist_filter

DAYS_WINDOW = 90
TOLERANCE_STEPS_SEC = tuple(range(0, 601, 60))
_ISO_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _avg_min(sum_delay_sec: Any, samples: Any) -> float:
    return round(float(sum_delay_sec) / float(samples) / 60.0, 2)


async def _grouped(agency_id: int, ctx: RangeCtx, conn: Any, key_sql: str) -> list:
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        f"SELECT {key_sql} AS key, SUM(samples) AS samples, SUM(sum_delay_sec) AS sum_delay_sec "
        f"FROM agg_route_daily_dist WHERE agency_id = $1 AND {where} "
        "GROUP BY 1 HAVING SUM(samples) > 0 ORDER BY 1"
    )
    return await conn.fetch(sql, agency_id, *params)


async def _merged_hist(agency_id: int, ctx: RangeCtx, conn: Any) -> list[int]:
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "SELECT u.i, SUM(u.h) AS c "
        "FROM agg_route_daily_dist, unnest(hist) WITH ORDINALITY u(h, i) "
        f"WHERE agency_id = $1 AND {where} GROUP BY u.i"
    )
    counts = [0] * N_BUCKETS
    for row in await conn.fetch(sql, agency_id, *params):
        counts[row["i"] - 1] = int(row["c"])
    return counts


async def compute_scope_summary(agency_id: int, ctx: RangeCtx, conn: Any, *, early_sec: int | None = None) -> dict:
    span = await conn.fetchrow(
        "SELECT MIN(date) AS earliest, MAX(date) AS latest FROM agg_route_daily_dist WHERE agency_id = $1",
        agency_id,
    )
    earliest, latest = span["earliest"], span["latest"]
    if latest is None:
        return {"earliest": None, "latest": None, "days": [], "weekdays": [], "routes": [], "tolerance": []}

    window = replace(ctx, from_date=max(earliest, latest - timedelta(days=DAYS_WINDOW - 1)), to_date=latest, dow="all")
    days = [
        {"date": r["key"], "avg_min": _avg_min(r["sum_delay_sec"], r["samples"]), "samples": int(r["samples"])}
        for r in await _grouped(agency_id, window, conn, "date")
    ]
    weekdays = [
        {
            "dow": _ISO_WEEKDAYS[int(r["key"]) - 1],
            "avg_min": _avg_min(r["sum_delay_sec"], r["samples"]),
            "samples": int(r["samples"]),
        }
        for r in await _grouped(agency_id, replace(ctx, dow="all"), conn, "EXTRACT(ISODOW FROM date)::int")
    ]
    routes = [
        {"route_code": r["key"], "avg_min": _avg_min(r["sum_delay_sec"], r["samples"]), "samples": int(r["samples"])}
        for r in await _grouped(agency_id, replace(ctx, routes=()), conn, "route_code")
    ]

    hist = await _merged_hist(agency_id, ctx, conn)
    total = sum(hist)
    low_sec = None if early_sec is None else -early_sec
    tolerance = (
        [
            {"late_sec": late, "on_time_pct": round(count_in_range(hist, low_sec, late) * 100.0 / total, 1)}
            for late in TOLERANCE_STEPS_SEC
        ]
        if total
        else []
    )
    return {
        "earliest": earliest,
        "latest": latest,
        "days": days,
        "weekdays": weekdays,
        "routes": routes,
        "tolerance": tolerance,
    }
