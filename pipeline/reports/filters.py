"""Shared SQL fragment builders: dedup CTE, ctx filters, time-band helpers."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from api.range import (
    TIME_BAND_RANGES,
    RangeCtx,
    build_agg_daily_trend_filter,
    build_updates_filter_ch,
    service_date_clause,
)
from pipeline.clickhouse import UPDATES_TABLE
from pipeline.db import build_dedup_ch_sql, build_stop_visits_ch_sql

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


def _service_label(ctx: RangeCtx) -> str | None:
    """The service filter a per-route report row was computed under, or None without one.

    The rankings and the dwell/run report are one row per route over all of
    its observations in ``ctx``. An observation's own ``service_type`` is the raw
    GTFS ``service_id`` it matched (blank when it matched none), so grouping by
    it would split one route into fragments, each averaged over a slice of its
    data. The service filter selects dates instead
    (:func:`api.range.resolve_service_dates`), and this label is that filter,
    kept in each row's service slot because the CSV export and the frontend's
    row tuples read the slots by position.
    """
    return None if ctx.service == "all" else ctx.service


def _dedup_cte_ch(
    ctx: RangeCtx,
    *,
    include_scheduled_sec: bool = False,
    table: str = UPDATES_TABLE,
) -> tuple[str, dict]:
    """ClickHouse-dialect dedup CTE builder.

    Wraps the shared latest-by-captured_at dedup SQL (`build_dedup_ch_sql`)
    in a `deduped` CTE, combined with `ctx`'s WHERE filter
    (`api.range.build_updates_filter_ch`). Every report/route/overview
    helper that needs the live (non-aggregated) `updates` table goes
    through this one builder so the dedup+filter shape can't drift between
    call sites.

    ``include_scheduled_sec`` forwards straight to `build_dedup_ch_sql` — pass
    it when the caller needs an hour-of-day bucket that doesn't silently drop
    after-midnight extended-hour trips (see that function's own docstring).
    A caller that needs arrivals reads :func:`_stop_visits_cte_ch` instead.

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
    body = build_dedup_ch_sql(
        extra_where=where,
        include_captured_at=False,
        include_scheduled_sec=include_scheduled_sec,
        table=table,
    )
    cte_sql = f"deduped AS ({body})"
    return cte_sql, params


def _stop_visits_cte_ch(
    ctx: RangeCtx,
    *,
    include_scheduled_sec: bool = False,
    table: str = UPDATES_TABLE,
) -> tuple[str, dict]:
    """:func:`_dedup_cte_ch`'s counterpart for a reader that pairs a stop's
    arrival with its departure: a ``visits`` CTE over
    `pipeline.db.build_stop_visits_ch_sql`, filtered by ``ctx`` the same way.
    Returns ``(cte_sql, parameters)``; ``agency_id`` is the caller's to add."""
    where, params = build_updates_filter_ch(ctx)
    body = build_stop_visits_ch_sql(extra_where=where, include_scheduled_sec=include_scheduled_sec, table=table)
    return f"visits AS ({body})", params


def _ch_rows(result) -> list[dict]:
    """Convert a clickhouse_connect ``QueryResult`` into a list of dict rows.

    Lets ported call sites keep the same ``r["col"]`` access pattern asyncpg
    ``Record``s already use, instead of positional-tuple unpacking at every
    site (mirrors the pattern already used ad hoc in api/routers/map.py).
    """
    cols = result.column_names
    return [dict(zip(cols, r, strict=True)) for r in result.result_rows]


def _dated_agg_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for a per-day aggregate keyed by (agency_id, date, route, service).

    Date and DOW come from :func:`api.range.build_agg_daily_trend_filter`
    (which keeps the DATE column bare so the primary-key prefix serves the
    range scan); the service filter's dates (:func:`api.range.service_date_clause`)
    and an optional ``route_code`` predicate are added here so every reader of
    ``agg_daily_trend`` and ``agg_route_daily_dist`` shares one filter shape.

    ``time_band`` is silently dropped — these tables roll up to (date, route,
    service) and have no hour-of-day column. When ``ctx.time_band != 'all'``
    callers must fall back to the live-updates path so the filter applies.
    """
    frag, params, n = build_agg_daily_trend_filter(ctx, next_param)
    parts: list[str] = [frag] if frag else []
    frag, p, n = service_date_clause("date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)
    if ctx.routes:
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1
    return " AND ".join(parts), params, n


def _agg_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """:func:`_dated_agg_filter` for ``agg_daily_trend``."""
    return _dated_agg_filter(ctx, next_param)


def _dist_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """:func:`_dated_agg_filter` for ``agg_route_daily_dist``."""
    return _dated_agg_filter(ctx, next_param)


def _time_band_sql_on(column: str, time_band: str, next_param: int) -> tuple[str, list, int]:
    """Optional WHERE fragment filtering ``column`` (a TIME column) to a
    named time-band window.

    Returns ``('', [], next_param)`` when ``time_band == 'all'`` or an
    unknown band name. Matches the asyncpg ``::text)::time`` cast pattern
    used elsewhere in this module (e.g. :func:`_agg_filter`'s siblings) for
    Postgres TIME columns.

    Unlike the ClickHouse builder (:func:`api.range.time_band_clause_ch`),
    the hour needs no modulo-24 wrap: the columns filtered here are Postgres
    TIME, and GTFS's extended hours (>= 24, a trip continuing past midnight)
    are resolved to NULL at ingest — see
    :func:`pipeline.strategies._time.parse_departure_time` — so a value
    outside ``00:00``–``23:59`` can never reach this predicate.
    """
    if time_band not in TIME_BAND_RANGES:
        return "", [], next_param
    start, end = TIME_BAND_RANGES[time_band]
    frag = f"{column}::time >= (${next_param}::text)::time AND {column}::time < (${next_param + 1}::text)::time"
    return frag, [start, end], next_param + 2
