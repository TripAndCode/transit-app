"""Shared SQL fragment builders: dedup CTE, ctx filters, time-band helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from api.range import RangeCtx, build_agg_daily_trend_filter, build_updates_filter_ch, dow_clause
from pipeline.db import build_dedup_ch_sql

# 2-dp minutes, matching the live ROUND(..., 2). Shared by every reports
# submodule that reconciles ClickHouse's round-half-to-even live path against
# Postgres's round-half-up agg path (overview.py, rankings.py).
_MIN = Decimal("0.01")


def _round2(x: float) -> Decimal:
    """Round an already-in-minutes float to 2 dp, half-up — matches Postgres
    ``ROUND(x::numeric, 2)``. Used to round ClickHouse live-path results in
    Python instead of ClickHouse's own ``round()`` (round-half-to-even),
    which would otherwise diverge from the agg fast path at exact .5
    boundaries for the same metric."""
    return Decimal(str(x)).quantize(_MIN, rounding=ROUND_HALF_UP)


def _dedup_cte_ch(ctx: RangeCtx) -> tuple[str, dict]:
    """ClickHouse-dialect dedup CTE builder.

    Wraps the shared latest-by-captured_at dedup SQL (`build_dedup_ch_sql`)
    in a `deduped` CTE, combined with `ctx`'s WHERE filter
    (`api.range.build_updates_filter_ch`). Every report/route/overview
    helper that needs the live (non-aggregated) `updates` table goes
    through this one builder so the dedup+filter shape can't drift between
    call sites.

    Returns ``(cte_sql, parameters)`` instead of a bare CTE fragment string,
    because ClickHouse parameters are a ``{name: value}`` dict passed to
    ``ch.query(..., parameters=...)``, not asyncpg positional ``$N`` args
    spliced into the fragment by the caller. ``parameters`` does NOT include
    ``agency_id`` — callers must add it themselves (``build_dedup_ch_sql``'s
    inner WHERE references ``{agency_id:UInt16}``), same as every other
    ClickHouse call site in this codebase (see e.g.
    ``pipeline.reports.rankings._route_wd_we_avg_ch``).
    """
    where, params = build_updates_filter_ch(ctx)
    cte_sql = f"deduped AS ({build_dedup_ch_sql(extra_where=where, include_captured_at=False)})"
    return cte_sql, params


def _ch_rows(result) -> list[dict]:
    """Convert a clickhouse_connect ``QueryResult`` into a list of dict rows.

    Lets ported call sites keep the same ``r["col"]`` access pattern asyncpg
    ``Record``s already use, instead of positional-tuple unpacking at every
    site (mirrors the pattern already used ad hoc in api/routers/map.py).
    """
    cols = result.column_names
    return [dict(zip(cols, r, strict=True)) for r in result.result_rows]


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


def _dist_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for ``agg_route_daily_dist`` (date + DOW + service + routes).

    Like :func:`_agg_filter` but the date predicate is kept **sargable on the
    real DATE column** — ``date >= $a AND date <= $b`` with the cast on the
    *parameter* side, not the column. ``agg_daily_trend`` stores ISO date
    *text* (forcing a ``date::date`` cast), but this table's ``date`` is a true
    DATE, so leaving the column uncast lets the ``(agency_id, date)`` PK prefix
    serve the range scan. ``time_band`` is unrepresentable here — callers fall
    back to the live path when ``ctx.time_band != 'all'``.
    """
    parts: list[str] = [f"date >= (${next_param}::text)::date AND date <= (${next_param + 1}::text)::date"]
    params: list = [str(ctx.from_date), str(ctx.to_date)]
    n = next_param + 2

    frag, p, n = dow_clause("date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)
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
    used elsewhere in this module (e.g. :func:`_agg_filter`'s siblings) for
    Postgres TIME columns.
    """
    if time_band == "all":
        return "", [], next_param
    if time_band not in _TIME_BAND_RANGES:
        return "", [], next_param
    start, end = _TIME_BAND_RANGES[time_band]
    frag = f"{column}::time >= (${next_param}::text)::time AND {column}::time < (${next_param + 1}::text)::time"
    return frag, [start, end], next_param + 2
