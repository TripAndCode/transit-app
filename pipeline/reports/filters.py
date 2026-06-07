"""Shared SQL fragment builders: dedup CTE, ctx filters, time-band helpers."""

from __future__ import annotations

from api.range import RangeCtx, build_agg_daily_trend_filter
from pipeline.db import build_dedup_inner_sql


def _dedup_cte(where_frag: str) -> str:
    """Wrap the shared latest-by-captured_at dedup SQL in a `deduped` CTE.

    `where_frag` is a trusted server-built fragment from
    `api.range.build_updates_filter` — never user input.
    """
    return f"deduped AS ({build_dedup_inner_sql(placeholder='$1', extra_where=where_frag)})"


def _agg_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for ``agg_daily_trend`` covering date + DOW + service + routes.

    Wraps :func:`api.range.build_agg_daily_trend_filter` (which only emits date
    + DOW) and tacks on optional ``service_type`` + ``route_code`` predicates
    so every Overview helper that reads ``agg_daily_trend`` shares the same
    filter shape.

    The ``time_band`` filter is silently dropped — the agg tables roll up to
    (date, route, service) granularity and have no hour-of-day column. When
    ``ctx.time_band != 'all'`` callers must fall back to the live-updates path
    so the filter actually applies.
    """
    frag, params, n = build_agg_daily_trend_filter(ctx, next_param)
    parts: list[str] = [frag] if frag else []
    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1
    if ctx.routes:
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1
    return " AND ".join(parts), params, n


# Mirrors api/range._TIME_BAND_RANGES. Duplicated locally so this module
# doesn't reach into a private name in another package.
_TIME_BAND_RANGES: dict[str, tuple[str, str]] = {
    "morning": ("05:00", "09:00"),
    "forenoon": ("09:00", "12:00"),
    "noon": ("12:00", "14:00"),
    "afternoon": ("14:00", "17:00"),
    "evening": ("17:00", "20:00"),
    "night": ("20:00", "24:00"),
    "late_night": ("00:00", "05:00"),
}


def _time_band_sql_on(column: str, time_band: str, next_param: int) -> tuple[str, list, int]:
    """Optional WHERE fragment filtering ``column`` (a TIME column) to a
    named time-band window.

    Returns ``('', [], next_param)`` when ``time_band == 'all'`` or an
    unknown band name. Matches the asyncpg ``::text)::time`` cast pattern
    that :func:`api.range.time_band_clause` uses for ``updates``.
    """
    if time_band == "all":
        return "", [], next_param
    if time_band not in _TIME_BAND_RANGES:
        return "", [], next_param
    start, end = _TIME_BAND_RANGES[time_band]
    frag = f"{column}::time >= (${next_param}::text)::time AND {column}::time < (${next_param + 1}::text)::time"
    return frag, [start, end], next_param + 2
