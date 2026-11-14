"""The 概況 magazine payload and its private stage-query helpers.

Public surface
--------------
compute_overview_summary(agency_id, ctx, conn, *, pool=None, ch=None) -> dict
    Build the full Overview tab payload.  Pass ``pool`` to run the
    stage queries concurrently (each task acquires its own connection);
    omit it (or pass ``None``) for the sequential single-connection path
    used by tests and ad-hoc callers.

Fast vs slow paths
------------------
Most helpers have two internal branches:

* **Fast path** (``ctx.time_band == "all"``) — reads the pre-aggregated
  ``agg_daily_trend`` / ``agg_hour_daily`` tables, sub-millisecond even
  over multi-month ranges.
* **Slow path** (any other time_band) — derives every figure from one shared
  grain (below), so the hour-of-day filter is honoured. The grain is read from
  ``agg_route_band_daily`` and ``agg_hour_daily`` (:func:`_fetch_band_aggregate`),
  and from the live ``updates`` table (ClickHouse, via
  ``pipeline.reports.filters._dedup_cte_ch``) only for what those can't
  answer: a band they can't split (:func:`_band_aggregate_answers`), an agency
  analyze has not yet built them for (:func:`_band_aggregate_built`), or the
  hour profiles under a ``routes`` filter.

The three hour profiles (``peak_hour`` and its weekday/weekend pair, all from
``_peak_hour_by_dow``) read the per-day ``agg_hour_daily`` (filtering dates by
the window, by DOW, and by the service filter's dates) on the fast path.
``agg_route_hour`` can't serve them: it has no date column, so it would answer
for every analysed day instead of the window. Under a ``routes`` filter they
read the shared grain instead, since ``agg_hour_daily`` aggregates across all
routes.

The shared slow-path grain
--------------------------
Read from the band aggregates, the grain costs two indexed Postgres reads and
no ClickHouse at all. The rest of this section is about the ClickHouse grain
it falls back to, whose cost grows with the period's rows.

The slow-path helpers share ONE round trip rather than each issuing its own
``_dedup_cte_ch`` scan of ``updates``: a dozen independent full dedup scans
of *substantially the same rows*, each reading a large fraction of an
agency's history over even a 30-day window, would put several of them over
``api.clickhouse.get_ch_client``'s ``max_execution_time`` cap and fail the
request. :func:`_fetch_grain` pre-aggregates the
dedup output once, in two grouping sets, ``(date, route_code)`` for the
route and day figures and ``(date, hour)`` for the hour profiles, and
each helper derives its own answer from the set it needs in Python. See
:func:`_fetch_grain` for why one grain can serve consumers with three
different date windows and two different DOW filters.

That is one round trip whenever the grain's span also contains
:func:`_route_weekly_history`'s wider ``weeks_back * 7``-day window — the
common case, including every default 30-day request. Only a ``ctx`` window too
narrow for the grain to reach that far back costs a second scan, and only when
``_movers`` found candidate routes to draw sparklines for.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Final, Iterable, Iterator, Literal, Mapping, Sequence

from api.range import (
    TIME_BAND_RANGES,
    RangeCtx,
    day_types_for,
    dow_clause,
    dow_isodays,
    resolve_service_dates,
    scheduled_hour_ch,
    service_date_clause,
)
from pipeline import perf
from pipeline.cache import async_lru_cache, cache_briefly
from pipeline.clickhouse import is_query_timeout, is_result_overflow
from pipeline.day_types import DayTypes
from pipeline.query.pooled_conn import PooledConnection
from pipeline.reports.filters import _agg_filter, _dedup_cte_ch, _round2, ctx_filing
from pipeline.reports.rankings import RANKING_MIN_SAMPLES, _round1
from pipeline.reports.reference import Coverage, reference_coverage, reference_reason, reference_window
from pipeline.reports.reference import reference_ctx as _reference_ctx
from pipeline.route_catalog import left_out_unless_named, route_list

_log = logging.getLogger(__name__)

# How many weekly buckets ``_movers`` draws each route's sparkline from
# (:func:`_route_weekly_history`), ending at the current window's last day.
_MOVER_HISTORY_WEEKS = 4


def _baseline_reach_days(ctx: RangeCtx) -> int:
    """How many days before ``ctx.from_date`` ``base_ctx`` can start:
    ``ctx``'s own width.

    :func:`compute_overview_summary` compares ``cur_ctx = [ctx.from_date,
    anchor]`` against ``base_ctx``, the same width immediately before it.
    ``anchor`` lies inside ``ctx``, so ``cur_ctx`` is at most ``ctx.days`` wide
    and ``base_ctx`` starts at most ``ctx.days`` before ``ctx.from_date``: the
    exact worst case, reached whenever the data runs to ``ctx.to_date``.
    """
    return ctx.days


def _grain_lookback_days(ctx: RangeCtx) -> int:
    """How many days before ``ctx.from_date`` the shared grain has to reach.

    Under a time band every figure reads the grain, ``base_ctx`` included, so
    it reaches :func:`_baseline_reach_days` back. Under a routes filter alone
    only the hour profiles read it, and only inside ``ctx``; every other figure
    reads its ``agg_*`` table, so the grain reaches back not at all. See
    :func:`_fetch_grain`; :func:`_grain_window` enforces the bound at runtime.
    """
    return _baseline_reach_days(ctx) if ctx.time_band != "all" else 0


def _calendar_from(ctx: RangeCtx) -> date:
    """The first day of the span the overview resolves its day types over.

    The payload reads before ``ctx.from_date`` twice, whichever store serves it:
    ``base_ctx`` reaches :func:`_baseline_reach_days` back, and the movers'
    sparkline ``_MOVER_HISTORY_WEEKS`` weeks back from a current window that
    ends inside ``ctx``. The day types are resolved once over the whole span
    (api.range.ServiceDates), so the service filter and the day-type split
    always agree. The reference resolves its own window instead
    (:func:`_reference`), so reaching for it cannot move the period's split.
    """
    return ctx.from_date - timedelta(days=max(_baseline_reach_days(ctx), 7 * _MOVER_HISTORY_WEEKS - 1))


def _listable(rows: Sequence[Sequence[Any]], left_out: Mapping[str, frozenset[str]]) -> list:
    """The ranked ``rows`` (route code first) a route list may show, in order:
    every row :func:`pipeline.route_catalog.route_list` keeps, however long.

    The overview's lists offer no toggle to keep a left-out code, so they leave
    each one out of what they list and of every count and share beside it.
    """
    return route_list(rows, len(rows), left_out, ())[0]


# How many pooled connections one overview request may hold at once on its
# pool-gather path. A dozen stages fan out per request; unbounded, two
# concurrent cold requests alone would exhaust the pool and queue every other
# endpoint behind them. The bound trades some of one request's overlap for
# room for several requests at once.
OVERVIEW_FANOUT_LIMIT = 4


# Shared slow-path grain

# A row of the grain's route set: (date, route_code, samples, sum_delay_sec,
# sum_late_sec), every service id of the route's day pooled: every figure
# files by route or by date, and the service filter is applied server-side.
# ``route_code`` is Nullable in ClickHouse.
# ``sum_delay_sec`` / ``sum_late_sec`` stay in SECONDS as exact integers, so
# summing partial groups in Python is lossless and the single trailing
# ``/ 60.0`` reproduces the SQL's own ``/ 60.0`` bit-for-bit.
_RouteRow = tuple[date, "str | None", int, int, int]
_SAMPLES: Final = 2
_SUM_DELAY: Final = 3
_SUM_LATE: Final = 4

# A row of the grain's hour set: (date, hour, samples, sum_delay_sec), pooled
# across routes and service ids. ``hour`` is never None: a departure with no
# scheduled time has no hour to profile, so the grain leaves it out of this set.
_HourRow = tuple[date, int, int, int]

# What one grain fetch reads (see _fetch_grain): both sets, the route set
# reaching back for the baseline; the period's route set alone; or the
# period's hour set alone.
_GrainShape = Literal["full", "period_routes", "period_hours"]


@dataclass(frozen=True)
class _Grain:
    """One request's worth of deduped departures: one ClickHouse round trip
    (:func:`_fetch_grain`), or the band aggregates' rows
    (:func:`_fetch_band_aggregate`).

    ``route_rows`` (the ``(date, route_code)`` set) span ``[from_date,
    to_date]``. ``hour_rows`` (the ``(date, hour)`` set) span ``[hours_from,
    to_date]``, the request's period alone. Either is None when the fetch's
    shape left its set out (:func:`_fetch_grain`). Both have ``agency_id`` +
    ``service`` + ``routes`` + ``time_band`` already applied, but deliberately
    no day-of-week filter: see :func:`_fetch_grain`.
    """

    route_rows: tuple[_RouteRow, ...] | None
    hour_rows: tuple[_HourRow, ...] | None
    from_date: date
    hours_from: date
    to_date: date


def _require_grain(grain: _Grain | None) -> _Grain:
    """Narrow an optional grain on a slow-path branch.

    Every helper below is module-private and only ever reached through
    :func:`compute_overview_summary`, which prefetches the grain whenever
    ``ctx.time_band != 'all'`` or a ``routes`` filter is set — i.e. exactly when
    a slow-path branch can be taken. An explicit raise rather than ``assert``
    so the invariant still holds under ``python -O``.
    """
    if grain is None:  # pragma: no cover - unreachable via the public entry point
        raise RuntimeError("overview slow path reached without a prefetched grain")
    return grain


def _dow_matches(d: date, dow: str) -> bool:
    """Python counterpart of :func:`api.range.dow_clause_ch`.

    ``dow_clause_ch`` filters on ``toDayOfWeek`` of the service date
    (:mod:`pipeline.service_day`) — mode 0, so 1=Monday..7=Sunday — and the
    grain's ``date`` column IS that same service date, so
    ``date.isoweekday()`` (also 1=Monday..7=Sunday) reproduces it exactly.
    """
    days = dow_isodays(dow)
    return days is None or d.isoweekday() in days


def _grain_covers(grain: _Grain | None, from_date: date, to_date: date) -> bool:
    """Whether ``grain``'s route rows actually span ``[from_date, to_date]``.

    The non-raising counterpart of :func:`_grain_routes`, for the one
    consumer whose window is a legitimate *maybe*: :func:`_route_weekly_history`
    asks for ``weeks_back * 7`` days ending at ``ctx.to_date``, which the grain
    contains for a wide ``ctx`` (every default 30-day request) and genuinely
    does not for a narrow one — so "not covered" there means "fall back to the
    live scan", not "invariant violated". Every other consumer's window is
    derived from ``ctx`` by construction and MUST be covered, which is why
    :func:`_grain_routes` raises for them.
    """
    return (
        grain is not None and grain.route_rows is not None and from_date >= grain.from_date and to_date <= grain.to_date
    )


def _grain_routes(grain: _Grain, from_date: date, to_date: date) -> tuple[_RouteRow, ...]:
    """The grain's route rows, once they are known to span ``[from_date, to_date]``.

    Fails loudly if a consumer asks for a window the grain never fetched.

    The grain's route rows span ``[ctx.from_date - _grain_lookback_days(ctx),
    ctx.to_date]``, which is derived from how
    :func:`compute_overview_summary` builds ``cur_ctx`` / ``base_ctx``. If that
    construction ever drifts — an off-by-one in a window's bounds, a baseline
    wider than the current window — the Python filters here would happily
    aggregate over a TRUNCATED window and return a plausible-looking but wrong
    user-facing average, with nothing to notice. Raising turns that class of
    regression into a visible failure instead of a quiet data bug.
    """
    if grain.route_rows is None or not _grain_covers(grain, from_date, to_date):
        span = "none" if grain.route_rows is None else f"[{grain.from_date}..{grain.to_date}]"
        raise RuntimeError(
            f"overview grain's route set ({span}) does not cover the requested window [{from_date}..{to_date}]"
        )
    return grain.route_rows


def _grain_hours(grain: _Grain, from_date: date, to_date: date) -> tuple[_HourRow, ...]:
    """The grain's hour rows, once they are known to span ``[from_date, to_date]``.

    The hour set's counterpart of :func:`_grain_routes`: hour rows span
    only the period, so a window reaching outside it, or a grain fetched
    without hours, raises rather than profiling a truncated window.
    """
    if grain.hour_rows is None or from_date < grain.hours_from or to_date > grain.to_date:
        span = "none" if grain.hour_rows is None else f"[{grain.hours_from}..{grain.to_date}]"
        raise RuntimeError(
            f"overview grain's hour set ({span}) does not cover the requested window [{from_date}..{to_date}]"
        )
    return grain.hour_rows


def _grain_window(grain: _Grain, ctx: RangeCtx) -> Iterator[_RouteRow]:
    """Route rows inside ``ctx``'s date window that satisfy ``ctx.dow``.

    This is the in-Python equivalent of the date-range + DOW half of
    ``build_updates_filter_ch(ctx)``; the other half (service / routes /
    time_band) was already applied server-side when the grain was fetched,
    because those three are identical across every consumer of one request.

    Not a generator function, so the coverage check fires when this is CALLED
    rather than when the result is first iterated.
    """
    rows = _grain_routes(grain, ctx.from_date, ctx.to_date)
    lo, hi, dow = ctx.from_date, ctx.to_date, ctx.dow
    return (row for row in rows if lo <= row[0] <= hi and _dow_matches(row[0], dow))


def _grain_avg_min(rows: Iterable[_RouteRow]) -> tuple[float | None, int]:
    """``(avg_min, samples)`` over ``rows`` — the ``avg(dep_delay) / 60.0``,
    ``count(*)`` pair the per-helper ClickHouse queries used to return.

    ``avg_min`` is None for zero input rows, mirroring Postgres's NULL-on-empty
    (and unlike ClickHouse's ``avg()``, which returns NaN over an empty input —
    see :func:`_headline_stats`). The division order ``(sum / n) / 60.0`` is
    deliberate: it matches ``avg(dep_delay) / 60.0``, whose numerator
    ClickHouse accumulates as an exact Int64 for an ``Int32`` column, so the
    two agree to the last bit (verified against live data).
    """
    n = 0
    total = 0
    for row in rows:
        n += row[_SAMPLES]
        total += row[_SUM_DELAY]
    if n == 0:
        return None, 0
    return (total / n) / 60.0, n


def _grain_sum_by(rows: Iterable[_RouteRow], key_index: int, value_index: int = _SUM_DELAY) -> dict:
    """``{group_key: (samples, summed_value_sec)}`` for one route-row column.

    ``value_index`` picks ``sum_delay_sec`` (the default — for averages) or
    ``sum_late_sec`` (``_SUM_LATE`` — for :func:`_concentration`'s lateness
    contribution).
    """
    acc: dict = {}
    for row in rows:
        slot = acc.get(row[key_index])
        if slot is None:
            acc[row[key_index]] = [row[_SAMPLES], row[value_index]]
        else:
            slot[0] += row[_SAMPLES]
            slot[1] += row[value_index]
    return {k: (v[0], v[1]) for k, v in acc.items()}


def _route_sums(grain: _Grain | None, ctx: RangeCtx, value_index: int = _SUM_DELAY) -> dict:
    """``{route_code: (samples, summed_value_sec)}`` over the grain's window,
    for the routes a list can name. ClickHouse's ``route_code`` is Nullable: a
    feed can send an observation no route resolves for. It can't be listed or
    acted on per route, and the aggregates the default path reads hold none
    (``route_code`` is NOT NULL there), so every route list and share leaves it
    out."""
    by_route = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=1, value_index=value_index)
    return {code: sums for code, sums in by_route.items() if code is not None}


async def _fetch_grain(agency_id: int, ctx: RangeCtx, conn, ch, *, shape: _GrainShape = "full") -> _Grain:
    """Fetch the whole request's deduped ``updates`` in ONE ClickHouse query.

    Under a time band it serves only a band the aggregates can't answer
    (:func:`_band_aggregate_answers`); its ``"period_hours"`` shape also
    serves the hour profiles under a routes filter.

    The slow-path stage helpers read this instead of each issuing its own
    ``_dedup_cte_ch`` scan (see the module docstring). Three things make a
    single shared scan possible:

    * ``service``, ``routes`` and ``time_band`` are IDENTICAL across every
      consumer within one ``compute_overview_summary`` call — ``cur_ctx`` and
      ``base_ctx`` copy them verbatim from ``ctx``, and ``_peak_hour_by_dow``
      reads ``ctx`` itself — so they are pushed into the shared server-side
      WHERE.
    * ``dow`` is NOT: ``_peak_hour_by_dow`` deliberately REPLACES ``ctx.dow``
      with ``weekday``/``weekend`` regardless of what the user chose. So no DOW
      filter is applied here at all; each consumer applies its own in Python
      off each row's ``date`` (:func:`_dow_matches`).
    * The date windows differ (``ctx`` / ``cur_ctx`` / ``base_ctx``), so the
      grain spans their union, ``[ctx.from_date - _grain_lookback_days(ctx),
      ctx.to_date]``, and each consumer re-filters. :func:`_baseline_reach_days`
      and :func:`_grain_lookback_days` give the bound and why it holds. Because
      the bound needs no knowledge of ``anchor``, ``_latest_data_date`` — which
      computes ``anchor`` — can be served from the grain too, instead of costing
      its own round trip.

    One consumer, :func:`_route_weekly_history`, asks for a window this bound
    does NOT guarantee: ``weeks_back * 7`` days back from ``cur_ctx.to_date``,
    which for the default ``weeks_back=4`` reaches ``anchor - 27``. The grain
    covers that whenever ``(anchor - ctx.from_date) + ctx.days >= 27`` — every
    request at least two weeks wide whose data runs to its last day, including
    every default 30-day request, but not a narrow one — so that helper checks
    coverage with :func:`_grain_covers` and keeps its own live scan for the
    uncovered case. Widening the grain to always cover it was rejected: it would
    add up to ``27 - ctx.days`` days of scan to every narrow slow-path request,
    most for the narrowest, to save a scan on the minority that find movers.

    The scan is grouped twice, as two grouping sets. The route set,
    ``(date, route_code)``, serves every figure that averages or ranks
    routes, days or windows. The hour set, ``(date, hour)``, serves the hour
    profiles, which pool every route anyway. Each row is filed under its set
    by ``grouping(route_code)``, never by which of its columns are NULL: a
    route row's own ``route_code`` can be NULL, and a column outside a row's
    set reads as its type's default. Only the hour profiles read ``hour``, and
    only inside ``ctx``, so a day before ``ctx.from_date`` gets no hour rows:
    its hour is NULL, and the ``HAVING`` drops every hour row without an hour.
    So the result is bounded by

    * route rows: the span's days (``ctx.days`` plus
      :func:`_grain_lookback_days`) times the ``route_code`` values observed
      on a day, and
    * hour rows: ``ctx.days`` times the hours departures run at, at most the
      hours of one service day's clock and fewer under a time band,

    which grows with the routes plus the hours, not with routes times hours.
    The route set dominates, and under a time band the lookback roughly
    doubles it.

    ``shape`` picks what the fetch reads. ``"full"`` reads both sets as
    above. ``"period_routes"`` reads the route set over ``ctx`` alone: a
    time-band request whose full grain passed ``get_ch_client``'s
    ``max_result_rows`` (which raises rather than truncating) or its
    ``max_execution_time`` reads it instead, and it serves every figure but
    the baseline's and the hour profiles (see
    :func:`compute_overview_summary`). It is cheaper for dropping the hour set
    and the per-row scheduled time behind it; its shorter date range holds
    less data only when the window before the period holds some. ``"period_hours"`` reads the hour set
    over ``ctx`` alone, which is all a routes filter without a time band reads
    from the grain; were that ever to fail, the caller degrades the hour
    profiles to null.

    The hour is the departure's hour on its service day's clock
    (``api.range.scheduled_hour_ch``), the one agg_hour_daily files it under:
    24 and on for a departure after midnight. A row with no scheduled time, or
    a malformed one, has a NULL hour instead of failing the one query the
    whole payload depends on, and so no hour row. Under ``time_band != 'all'``
    none reach the grain, since the band filter places no such row in any
    band.
    """
    lookback = _grain_lookback_days(ctx) if shape == "full" else 0
    span_ctx = RangeCtx(
        from_date=ctx.from_date - timedelta(days=lookback),
        to_date=ctx.to_date,
        dow="all",  # applied per-consumer in Python; see the docstring
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
        service_dates=ctx.service_dates,
    )
    filing = await ctx_filing(conn, ch, agency_id, span_ctx)
    cte_sql, ch_params = _dedup_cte_ch(span_ctx, filing, include_scheduled_sec=shape != "period_routes")
    params = {"agency_id": agency_id, **ch_params}
    hour_expr = scheduled_hour_ch()
    if shape == "full":
        hour_expr = f"if(date >= {{grain_hour_from:Date}}, {hour_expr}, NULL)"
        params["grain_hour_from"] = ctx.from_date
        keys = f"route_code, {hour_expr} AS hour, grouping(route_code) AS hour_set"
        grouping = (
            f"GROUP BY GROUPING SETS ((date, route_code), (date, {hour_expr}))\nHAVING hour_set = 0 OR hour IS NOT NULL"
        )
    elif shape == "period_routes":
        keys = "route_code, NULL AS hour, 0 AS hour_set"
        grouping = "GROUP BY date, route_code"
    else:
        keys = f"NULL, {hour_expr} AS hour, 1 AS hour_set"
        grouping = f"GROUP BY date, {hour_expr}\nHAVING hour IS NOT NULL"
    result = await ch.query(
        f"WITH {cte_sql}\n"
        f"SELECT date, {keys},\n"
        "       count(*) AS samples,\n"
        "       sum(dep_delay) AS sum_delay,\n"
        "       sum(greatest(dep_delay, 0)) AS sum_late\n"
        "FROM deduped\n"
        f"{grouping}",
        parameters=params,
    )
    route_rows: list[_RouteRow] = []
    hour_rows: list[_HourRow] = []
    for d, route_code, hour, hour_set, samples, sum_delay, sum_late in result.result_rows:
        if hour_set:
            hour_rows.append((d, hour, int(samples), int(sum_delay or 0)))
        else:
            route_rows.append((d, route_code, int(samples), int(sum_delay or 0), int(sum_late or 0)))
    return _Grain(
        route_rows=None if shape == "period_hours" else tuple(route_rows),
        hour_rows=None if shape == "period_routes" else tuple(hour_rows),
        from_date=span_ctx.from_date,
        hours_from=ctx.from_date,
        to_date=span_ctx.to_date,
    )


def _band_aggregate_answers(ctx: RangeCtx) -> bool:
    """Whether the band aggregates can stand in for :func:`_fetch_grain` under
    ``ctx``'s time band. ``agg_route_band_daily`` holds every band's route
    rows; ``agg_hour_daily`` holds its hour rows only when both of the band's
    edges fall on the hour (:func:`_whole_hour_band`)."""
    return _whole_hour_band(ctx.time_band) is not None


async def _fetch_band_aggregate(agency_id: int, ctx: RangeCtx, conn, hours: tuple[int, int]) -> _Grain:
    """:func:`_fetch_grain`'s rows for a time band, read from Postgres.

    The route rows come from ``agg_route_band_daily``, whose ``''`` route is
    the grain's NULL one; the hour rows from ``agg_hour_daily``, an hour taken
    modulo 24 against the band's ``hours`` (:func:`_whole_hour_band`) as the
    band filter takes the clock.
    That table pools every route, so under a ``routes`` filter the grain has
    no hour rows and the caller fetches them. Both are the same integer sums
    the grain groups, so every figure derived from them is the grain's.

    The route rows reach back to :func:`_calendar_from`, the earliest day the
    payload reads, so :func:`_route_weekly_history` reads them too rather than
    scanning ``updates`` for a window narrower than its four weeks.
    """
    span_ctx = replace(ctx, from_date=_calendar_from(ctx))
    service_frag, service_params, n = service_date_clause("date", span_ctx, 5)
    routes_frag = ""
    if ctx.routes:
        routes_frag = f" AND route_code = ANY(${n}::text[])"
        service_params = [*service_params, list(ctx.routes)]
    route_rows = await conn.fetch(
        "SELECT date, route_code, samples, sum_delay_sec, sum_late_sec FROM agg_route_band_daily\n"
        "WHERE agency_id = $1 AND time_band = $2 AND date >= $3 AND date <= $4\n"
        f"  AND {service_frag}{routes_frag}",
        agency_id,
        ctx.time_band,
        span_ctx.from_date,
        ctx.to_date,
        *service_params,
    )
    hour_rows = None
    if not ctx.routes:
        service_frag, service_params, _ = service_date_clause("date", ctx, 6)
        fetched = await conn.fetch(
            "SELECT date, hour, samples, sum_delay_sec FROM agg_hour_daily\n"
            "WHERE agency_id = $1 AND date >= $2 AND date <= $3 AND hour % 24 >= $4 AND hour % 24 < $5\n"
            f"  AND sum_delay_sec IS NOT NULL AND {service_frag}",
            agency_id,
            ctx.from_date,
            ctx.to_date,
            *hours,
            *service_params,
        )
        hour_rows = tuple((r["date"], int(r["hour"]), int(r["samples"]), int(r["sum_delay_sec"])) for r in fetched)
    return _Grain(
        route_rows=tuple(
            (r["date"], r["route_code"] or None, int(r["samples"]), int(r["sum_delay_sec"]), int(r["sum_late_sec"]))
            for r in route_rows
        ),
        hour_rows=hour_rows,
        from_date=span_ctx.from_date,
        hours_from=ctx.from_date,
        to_date=ctx.to_date,
    )


async def _band_aggregate_built(agency_id: int, conn) -> bool:
    """Whether analyze has built ``agg_route_band_daily`` for the agency.

    An agency analyzed only before the table existed has no rows in it until
    its next run, which says nothing about its departures, so its empty rows
    are no answer; a built table's empty rows mean the band saw none.
    """
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM agg_table_state WHERE agency_id = $1 AND table_name = $2)",
            agency_id,
            "agg_route_band_daily",
        )
    )


async def _latest_data_date(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> date | None:
    """Most recent date inside ctx that has any samples.

    Ends the headline's current window, and so sizes its same-width baseline,
    where data actually exists: when ingest lags behind ``ctx.to_date``, the
    empty trailing days count toward neither window's width.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` for
    sub-millisecond response. Slow path (any other time band) reads the shared
    grain (:func:`_fetch_grain`), which honours the same filters — it replaces
    a ``maxOrNull(date) FROM deduped`` scan of live ``updates``.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = f"SELECT MAX(date) AS d FROM agg_daily_trend WHERE agency_id=$1{where_clause}"
        row = await conn.fetchrow(sql, agency_id, *params)
        return row["d"] if row and row["d"] else None

    dates = [row[0] for row in _grain_window(_require_grain(grain), ctx)]
    return max(dates) if dates else None


async def _headline_stats(
    agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None
) -> tuple[float | None, int]:
    """Return (avg_min, samples) for the headline over ``ctx``.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` as a
    sample-weighted average so days with more observations weigh
    proportionally. Slow path (any other time band) reads the shared grain
    (:func:`_fetch_grain`) so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = (
            # The count sits beside a sum_delay_sec-derived average, so it
            # counts the rows that average covers and FILTERs with it. The
            # ClickHouse path below returns an exact count of its own average's
            # rows, so only the filtered figure is the same statistic whichever
            # path answered.
            "SELECT CASE WHEN SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 0\n"
            "            THEN ROUND((SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
            "                / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0), 2)\n"
            "            ELSE NULL END AS avg_min,\n"
            "       COALESCE(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}"
        )
        row = await conn.fetchrow(sql, agency_id, *params)
        avg = float(row["avg_min"]) if row["avg_min"] is not None else None
        return avg, int(row["samples"] or 0)

    # `_grain_avg_min` returns None (not ClickHouse's NaN) for zero input rows
    # and guards on `samples` — an exact row count — matching Postgres's
    # NULL-on-empty semantics that the rest of this codebase (and its JSON
    # consumers) rely on. Round in Python (half-up) to match Postgres ROUND() —
    # ClickHouse's own round() is round-half-to-even and would otherwise
    # diverge from the agg fast path at exact .5-minute boundaries.
    avg_min_raw, samples = _grain_avg_min(_grain_window(_require_grain(grain), ctx))
    avg = float(_round2(avg_min_raw)) if samples > 0 and avg_min_raw is not None else None
    return avg, samples


async def _per_route_avg(
    agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None
) -> dict[str, tuple[float, int]]:
    """Per-route avg_min + samples for ``ctx``. Keyed by route_code.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average. Slow path reads the shared grain
    (:func:`_fetch_grain`) so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = (
            "SELECT route_code,\n"
            # The count sits beside a sum_delay_sec-derived average, so it
            # counts that average's rows and FILTERs with it. Two reasons it
            # cannot be the unfiltered total here: the slow path below sums a
            # ClickHouse row count where every row carries a dep_delay, so an
            # unfiltered fast path would answer differently for the same
            # route; and _movers and _top_delayed_routes gate
            # MOVER_MIN_SAMPLES on this number, which would otherwise admit a
            # route whose average rests on far fewer rows than the floor
            # implies.
            "       (SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
            "           / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min,\n"
            "       SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 0"
        )
        rows = await conn.fetch(sql, agency_id, *params)
        return {r["route_code"]: (float(r["avg_min"]), int(r["samples"])) for r in rows}

    # Grouping by route_code means every emitted group has >= 1 sample, so
    # unlike _headline_stats there's no empty-input case to guard here. Values
    # stay UNROUNDED: _movers derives its deltas from them and does its own
    # rounding. The keys go on into _route_weekly_history's Array(String)
    # parameter, which a NULL route would fail.
    return {route_code: ((total / n) / 60.0, n) for route_code, (n, total) in _route_sums(grain, ctx).items()}


async def _route_short_names(agency_id: int, route_codes: list[str], conn) -> dict[str, str | None]:
    """Resolve route_short_name for a list of route_codes.

    `route_code` is the digit suffix inside `route_id`'s trailing `(NNNN)`
    (same regex used by api/routers/static.py:list_routes end-to-end).
    Routes without a matching static_routes row map to None.
    """
    if not route_codes:
        return {}
    rows = await conn.fetch(
        "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, "
        "       route_short_name "
        "FROM static_routes "
        "WHERE agency_id=$1 "
        "  AND regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') = ANY($2::text[])",
        agency_id,
        list(route_codes),
    )
    return {r["route_code"]: r["route_short_name"] for r in rows}


async def _route_weekly_history(
    agency_id: int,
    route_codes: list[str],
    ctx: RangeCtx,
    conn,
    weeks_back: int = 4,
    ch=None,
    grain: _Grain | None = None,
) -> dict[str, list[float | None]]:
    """Per-route weekly avg_min for the last ``weeks_back`` true 7-day
    buckets ending at ``ctx.to_date``. Honors DOW / service / routes.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` — one
    small indexed query per week (cheap; the table has no dedup to redo).

    Slow path prefers the shared grain (:func:`_fetch_grain`) when it reaches
    far enough back, and only falls back to its own live ``updates``
    (ClickHouse) scan when it doesn't. The grain reaches
    :func:`_grain_lookback_days` before the payload's period, while this
    function needs ``[ctx.to_date - (7 * weeks_back - 1), ctx.to_date]``, so
    for the default ``weeks_back=4`` the grain covers it whenever the period is
    at least two weeks wide and its data runs to its last day — i.e. every
    default 30-day request, which is the common case. That matters because
    this was the second ClickHouse round trip of an otherwise one-round-trip
    slow path, on top of the grain's own scan cost, and it fires whenever
    ``_movers`` has any candidate routes at all.

    The live fallback (narrow ``ctx`` only) is ONE dedup scan over the full
    ``weeks_back * 7``-day span, bucketed by week index, rather than
    ``weeks_back`` separate dedup scans of live `updates` (one per week):
    ``date`` is part of the dedup key, so re-running the same argMax dedup once
    over the whole span and then grouping by a week index is equivalent to the
    union of the per-week queries, at 1/``weeks_back`` the round trips.
    """
    if not route_codes:
        return {}

    out: dict[str, list[float | None]] = {code: [] for code in route_codes}

    if ctx.time_band == "all":
        for k in range(weeks_back - 1, -1, -1):
            end = ctx.to_date - timedelta(days=7 * k)
            start = end - timedelta(days=6)  # inclusive 7-day window
            window_ctx = RangeCtx(
                from_date=start,
                to_date=end,
                dow=ctx.dow,
                time_band=ctx.time_band,
                service=ctx.service,
                routes=ctx.routes,
                service_dates=ctx.service_dates,
            )
            where, params, n = _agg_filter(window_ctx, next_param=2)
            where_clause = f" AND ({where})" if where else ""
            rows = await conn.fetch(
                "SELECT route_code,\n"
                # sum_delay_sec is nullable (unlike samples); a row can have
                # samples set but sum_delay_sec still NULL (pre-backfill row,
                # hand-seeded fixture, or any row analyze() hasn't rewritten
                # since migration 0028) — FILTER both sides to the same row
                # population so SUM(samples) never counts a row SUM(sum_delay_sec)
                # silently dropped, matching pipeline/digest/build.py's
                # _ROUTE_BASELINE_SQL.
                "       (SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
                "           / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min\n"
                "FROM agg_daily_trend\n"
                f"WHERE agency_id=$1{where_clause}\n"
                f"  AND route_code = ANY(${n}::text[])\n"
                "GROUP BY route_code",
                agency_id,
                *params,
                list(route_codes),
            )
            wk_map = {r["route_code"]: float(r["avg_min"]) for r in rows if r["avg_min"] is not None}
            for code in route_codes:
                out[code].append(wk_map.get(code))
        return out

    span_from = ctx.to_date - timedelta(days=7 * weeks_back - 1)

    if _grain_covers(grain, span_from, ctx.to_date):
        # The shared grain already holds every row this span needs, so derive
        # the weekly buckets in Python instead of issuing a second full dedup
        # scan. This reproduces the ClickHouse branch below exactly:
        #
        # * the same rows — the grain applied `agency_id` + `service` +
        #   `routes` + `time_band` server-side (identical to `span_ctx`'s, all
        #   copied from the same `ctx`), the `route_code IN (...)` and date-span
        #   predicates are applied here, and `_dow_matches(_, ctx.dow)` is the
        #   Python counterpart of `span_ctx`'s own `dow_clause_ch`;
        # * the same week index — `intDiv(dateDiff('day', date, to_date), 7)`
        #   is `(to_date - date).days // 7`;
        # * the same arithmetic — grain sums are exact integer seconds, so
        #   `(sum / n) / 60.0` matches `avg(dep_delay) / 60.0` bit-for-bit
        #   (see :func:`_grain_avg_min`), and `count(*)` is `count(dep_delay)`
        #   because the dedup CTE already drops NULL `dep_delay` rows.
        wanted = set(route_codes)
        acc: dict[tuple[str, int], list[int]] = {}
        for row in _grain_routes(_require_grain(grain), span_from, ctx.to_date):
            d, route_code = row[0], row[1]
            if route_code not in wanted or not (span_from <= d <= ctx.to_date):
                continue
            if not _dow_matches(d, ctx.dow):
                continue
            slot = acc.setdefault((route_code, (ctx.to_date - d).days // 7), [0, 0])
            slot[0] += row[_SAMPLES]
            slot[1] += row[_SUM_DELAY]
        for code in route_codes:
            for k in range(weeks_back - 1, -1, -1):
                bucket = acc.get((code, k))
                out[code].append(((bucket[1] / bucket[0]) / 60.0) if bucket else None)
        return out

    # Live ClickHouse path (reached only when the grain doesn't reach back far
    # enough — a period narrower than two weeks, or one whose data stops before
    # its end): one dedup CTE over the
    # whole [ctx.to_date - weeks_back*7 + 1, ctx.to_date] span, bucketed into
    # weeks_back week-index groups via `intDiv(dateDiff('day', date, to_date),
    # 7)` — 0 is the most recent 7-day bucket ending at ctx.to_date,
    # weeks_back-1 the oldest, matching the fast path's per-week loop's `k` and its
    # oldest-first append order into `out[code]`.
    if ch is None:
        raise RuntimeError("_route_weekly_history's live fallback requires a ClickHouse client")
    span_ctx = RangeCtx(
        from_date=span_from,
        to_date=ctx.to_date,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
        service_dates=ctx.service_dates,
    )
    cte_sql, ch_params = _dedup_cte_ch(span_ctx, await ctx_filing(conn, ch, agency_id, span_ctx))
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        "       intDiv(dateDiff('day', date, {rw_to_date:Date}), 7) AS wk,\n"
        "       avg(dep_delay) / 60.0 AS avg_min\n"
        "FROM deduped\n"
        "WHERE route_code IN {rw_route_codes:Array(String)}\n"
        "GROUP BY route_code, wk",
        parameters={
            "agency_id": agency_id,
            "rw_route_codes": list(route_codes),
            "rw_to_date": ctx.to_date,
            **ch_params,
        },
    )
    by_route_wk: dict[tuple[str, int], float] = {
        (route_code, wk): float(avg_min) for route_code, wk, avg_min in result.result_rows
    }
    for code in route_codes:
        for k in range(weeks_back - 1, -1, -1):
            out[code].append(by_route_wk.get((code, k)))
    return out


def _streak_weeks(history: list[float | None], *, direction: str) -> int:
    """Count trailing consecutive weeks where each week is worse (up) or
    better (down) than the prior week. Stops at the first non-matching or
    None pair. Caller passes oldest-first history; we scan from end backwards."""
    if len(history) < 2:
        return 0
    count = 0
    for i in range(len(history) - 1, 0, -1):
        cur = history[i]
        prev = history[i - 1]
        if cur is None or prev is None:
            break
        if direction == "up" and cur > prev:
            count += 1
        elif direction == "down" and cur < prev:
            count += 1
        else:
            break
    return count


async def _concentration(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    grain: _Grain | None = None,
    *,
    left_out: Mapping[str, frozenset[str]],
) -> dict:
    """Top-20 routes by total positive delay contribution + rest share, both
    over the routes :func:`_listable` keeps.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend``'s
    ``sum_late_sec`` — the exact per-row ``SUM(GREATEST(dep_delay, 0))`` — and
    sums it directly per route, in minutes. This is a TOTAL, not a mean, so
    pooling multiple day-rows is a plain SUM with no division; unlike
    ``avg_min``, clamping a signed value to zero BEFORE summing across
    observations would understate (never overstate) a route's true lateness
    contribution whenever a day mixes early and late trips — the day's mean
    can land at or below zero while individual late trips still contributed
    real lateness. ``sum_late_sec`` avoids that by clamping each observation
    before it is ever summed, so the fast and slow paths agree exactly on this
    metric (mirroring ``sum_delay_sec``'s exact-vs-rounded pooling fix, but for
    the clamped total rather than the signed mean).

    Slow path (any non-default time band) reads the shared grain
    (:func:`_fetch_grain`), whose ``sum_late_sec`` column is the same per-row
    ``SUM(GREATEST(dep_delay, 0))`` computed exactly, so the hour-of-day filter
    is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        fetched = await conn.fetch(
            "SELECT route_code,\n"
            # sum_late_sec is nullable (not backfilled — see migration 0029):
            # a row analyze() hasn't rewritten since that migration contributes
            # 0 here (Postgres SUM skips NULLs), understating rather than
            # crashing or looking like a genuine zero-lateness day.
            "       (SUM(sum_late_sec)::numeric / 60.0) AS total_late_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "ORDER BY total_late_min DESC NULLS LAST, route_code",
            agency_id,
            *params,
        )
        rows = [(r["route_code"], float(r["total_late_min"] or 0.0)) for r in fetched]
    else:
        # Grouping by route_code means every emitted group has >= 1 sample (no
        # empty-input case), and the summed lateness is never NULL for a
        # non-empty group, so no ORDER BY ... NULLS LAST equivalent is needed.
        # Ties break on route_code, so the top-20 cut is reproducible: the
        # pooling gives no order within a tie.
        by_route = _route_sums(grain, ctx, value_index=_SUM_LATE)
        rows = [(code, late / 60.0) for code, (_n, late) in sorted(by_route.items(), key=lambda kv: (-kv[1][1], kv[0]))]
    rows = _listable(rows, left_out)
    if not rows:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    grand_total = sum(late for _code, late in rows)
    if grand_total == 0:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    top_n = rows[:20]
    names = await _route_short_names(agency_id, [code for code, _late in top_n], conn)
    top_n_sum = sum(late for _code, late in top_n)
    return {
        "top_routes": [
            {
                "route_code": code,
                "route_short_name": names.get(code),
                "share_pct": round((late / grand_total) * 100.0, 1),
            }
            for code, late in top_n
        ],
        "rest_share_pct": round(((grand_total - top_n_sum) / grand_total) * 100.0, 1),
        "rest_route_count": max(len(rows) - len(top_n), 0),
    }


#: A route averaging at least this late (minutes) over the current window
#: counts as delayed in ``delayed_count``. The count carries it, so a reader
#: states the threshold it was counted against instead of keeping its own copy.
DELAYED_ROUTE_MIN = 2.0

#: The fewest observations a route needs in the current window to be listed
#: or counted by ``_top_delayed_routes``: the Routes ranking's default floor,
#: so the list's "see every route" link opens on the same routes.
TOP_DELAYED_MIN_SAMPLES = RANKING_MIN_SAMPLES

#: The fewest observations in EACH of two windows behind a change between
#: them: a mover's, and a listed route's change from the window before. A
#: listed route clears it in the current window by the list's own floor,
#: which a test holds at or above this one.
MOVER_MIN_SAMPLES = 10

#: The routes-to-check list's bands, worst first, each with its lower bound
#: (minutes): a route belongs to the first band whose bound its average
#: reaches, and to none below the last. The client bands the listed routes
#: itself, with BAND_BOUNDS in frontend/src/components/routesToCheckBands.ts;
#: a test on each side pins the same bounds, so neither side can move alone.
#: Each count carries its bound, as ``delayed_threshold_min`` does.
ROUTE_BAND_MIN = {"severe": 5.0, "moderate": 3.0, "mild": 1.5}


def _route_band_counts(avgs: list[float]) -> dict[str, dict[str, float | int]]:
    """How many of ``avgs`` fall in each of ``ROUTE_BAND_MIN``'s bands.

    Banded on the 2-dp rounded average each listed route carries, the value
    the client bands it on, so a route listed at exactly a bound is always
    counted in that band.
    """
    counts = {band: 0 for band in ROUTE_BAND_MIN}
    for avg in avgs:
        rounded = float(_round2(avg))
        band = next((b for b, lower in ROUTE_BAND_MIN.items() if rounded >= lower), None)
        if band is not None:
            counts[band] += 1
    return {band: {"count": counts[band], "threshold_min": lower} for band, lower in ROUTE_BAND_MIN.items()}


async def _top_delayed_routes(
    agency_id: int,
    cur_ctx: RangeCtx,
    conn,
    limit: int = 5,
    ch=None,
    grain: _Grain | None = None,
    *,
    left_out: Mapping[str, frozenset[str]],
    base_ctx: RangeCtx | None = None,
) -> dict:
    """Routes ranked by absolute current-window avg delay ("routes to check
    now"), plus a count of routes at/above ``DELAYED_ROUTE_MIN``, one per
    ``ROUTE_BAND_MIN`` band, and ``route_count``, the routes all of them are
    out of. Every count is over every route :func:`_listable` keeps that was
    observed at least ``TOP_DELAYED_MIN_SAMPLES`` times in the window (echoed
    as ``min_samples``), not only the ``limit`` listed: the list is truncated,
    the counts are not.

    Each listed route carries ``delta_min``, its change from ``base_ctx`` (the
    same-width window before), by the movers' rule: the unrounded current
    average less the unrounded previous one, and None unless the route has
    ``MOVER_MIN_SAMPLES`` observations there. The movers list only the routes
    that moved most, so it cannot supply a change for every listed route.

    Uses cur_ctx, the current window compute_overview_summary builds for the
    headline (the period up to its last day with data), so the KPI row's
    stats and the routes list all describe the same days.

    Fast path mirrors _concentration()'s: reads agg_daily_trend, but computes
    each route's true weighted average (SUM(sum_delay_sec)/SUM(samples),
    dividing once at the end rather than re-weighting each day's already-
    rounded avg_min — see pipeline/analyze.py's module docstring), not
    _concentration()'s "total lateness contribution" sum — a route with
    few samples but a high average must outrank a route with more samples
    but a lower average, which _concentration()'s metric would get backwards.
    Slow path reads the shared grain (:func:`_fetch_grain`) for a non-default
    time_band, same as _concentration().
    """
    if cur_ctx.time_band == "all":
        where, params, _ = _agg_filter(cur_ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        fetched = await conn.fetch(
            "SELECT route_code,\n"
            # sum_delay_sec is nullable (unlike samples); FILTER both sides to
            # the same row population — see _route_weekly_history's identical
            # rationale.
            "       SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
            "           / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0 AS avg_min,\n"
            "       SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            # Guard against a NULL pooled average reaching the unguarded
            # _round2(...) below.
            "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 0\n"
            # Ties broken by route_code, same as the slow path just below and
            # as _concentration()'s fast path.
            "ORDER BY avg_min DESC NULLS LAST, route_code",
            agency_id,
            *params,
        )
        rows = [(r["route_code"], r["avg_min"]) for r in fetched if r["samples"] >= TOP_DELAYED_MIN_SAMPLES]
    else:
        # Grouping by route_code -> every group has >= 1 sample, no
        # empty-input case; the average is never NULL for a non-empty group.
        # Ties broken by route_code, as in _concentration().
        by_route = _route_sums(grain, cur_ctx)
        rows = [
            (code, (total / n) / 60.0)
            for code, (n, total) in sorted(by_route.items(), key=lambda kv: (-(kv[1][1] / kv[1][0]), kv[0]))
            if n >= TOP_DELAYED_MIN_SAMPLES
        ]

    rows = _listable(rows, left_out)
    if not rows:
        return {
            "routes": [],
            "delayed_count": 0,
            "delayed_threshold_min": DELAYED_ROUTE_MIN,
            "band_counts": _route_band_counts([]),
            "route_count": 0,
            "min_samples": TOP_DELAYED_MIN_SAMPLES,
        }

    delayed_count = sum(1 for _code, avg in rows if avg is not None and avg >= DELAYED_ROUTE_MIN)
    band_counts = _route_band_counts([avg for _code, avg in rows if avg is not None])
    top_n = rows[:limit]
    listed = [code for code, _avg in top_n]
    names = await _route_short_names(agency_id, listed, conn)
    # Narrowed to the listed routes: the window before is wanted only for them.
    previous = (
        await _per_route_avg(agency_id, replace(base_ctx, routes=tuple(listed)), conn, ch=ch, grain=grain)
        if base_ctx is not None
        else {}
    )

    def _change(code: str, avg) -> float | None:
        prv_avg, prv_n = previous.get(code, (None, 0))
        if prv_avg is None or prv_n < MOVER_MIN_SAMPLES:
            return None
        return float(_round2(float(avg) - prv_avg))

    return {
        "routes": [
            {
                "route_code": code,
                "route_short_name": names.get(code),
                "avg_min": float(_round2(avg)),
                "delta_min": _change(code, avg),
            }
            for code, avg in top_n
        ],
        "delayed_count": delayed_count,
        "delayed_threshold_min": DELAYED_ROUTE_MIN,
        "band_counts": band_counts,
        "route_count": len(rows),
        "min_samples": TOP_DELAYED_MIN_SAMPLES,
    }


async def _peak_hour_by_dow(
    agency_id: int, ctx: RangeCtx, conn, dow_group: str, ch=None, grain: _Grain | None = None
) -> dict | None:
    """Hour-by-hour avg delay over ``ctx``'s window and the days of the week
    ``dow_group`` names, plus the peak hour: ``ctx.dow`` for the payload's
    ``peak_hour``, ``'weekday'``/``'weekend'`` for its pair.

    Fast path reads the per-day/hour ``agg_hour_daily`` (filtering dates by
    DOW and by the service filter's dates), a sample-weighted average across
    the range — sub-second, where the raw dedup scan would dominate
    Overview's cold load. That table is aggregated across all routes, so a
    ``routes`` filter, or any ``time_band`` other than ``'all'``, has to leave
    it for the shared grain, which :func:`compute_overview_summary` prefetches
    for exactly those shapes. One scan then serves all three profiles.

    ``dow_group`` REPLACES ``ctx.dow``: the pair asks "weekday vs weekend",
    regardless of any day-of-week the user had already narrowed to. That is
    exactly why the grain carries no DOW filter of its own (see
    :func:`_fetch_grain`).
    """
    if ctx.time_band == "all" and not ctx.routes:
        dow_frag, _, n = dow_clause("date", replace(ctx, dow=dow_group), 4)
        service_frag, service_params, _ = service_date_clause("date", ctx, n)
        sql = (
            "SELECT hour AS h,\n"
            # sum_delay_sec is nullable (unlike samples); FILTER both sides to
            # the same row population — see _route_weekly_history's identical
            # rationale.
            "       (SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
            "           / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min\n"
            "FROM agg_hour_daily\n"
            "WHERE agency_id = $1 AND date >= ($2::text)::date AND date <= ($3::text)::date\n"
            f"  AND {dow_frag} AND {service_frag}\n"
            "GROUP BY hour"
        )
        rows = await conn.fetch(sql, agency_id, str(ctx.from_date), str(ctx.to_date), *service_params)
        return _peak_from_hour_rows(rows)

    by_hour: dict[int, list[int]] = {}
    lo, hi = ctx.from_date, ctx.to_date
    for d, hour, samples, sum_delay in _grain_hours(_require_grain(grain), lo, hi):
        if not (lo <= d <= hi) or not _dow_matches(d, dow_group):
            continue
        slot = by_hour.setdefault(hour, [0, 0])
        slot[0] += samples
        slot[1] += sum_delay
    return _peak_from_hour_rows([{"h": h, "avg_min": (total / n) / 60.0} for h, (n, total) in sorted(by_hour.items())])


def _peak_from_hour_rows(rows) -> dict | None:
    """Shape ``(h, avg_min)`` rows into the ``by_hour`` + peak payload.

    ``by_hour`` holds the service day's hours: 0..23, and on through the
    latest hour after midnight (24, 25, ...) a row has.
    """
    if not rows:
        return None
    hours = [int(r["h"]) for r in rows if r["avg_min"] is not None and int(r["h"]) >= 0]
    by_hour: list[float | None] = [None] * (max([23, *hours]) + 1)
    for r in rows:
        if r["avg_min"] is None:
            continue
        h = int(r["h"])
        if h >= 0:
            by_hour[h] = float(_round2(r["avg_min"]))
    valid = [h for h in range(len(by_hour)) if by_hour[h] is not None]
    if not valid:
        return None
    # `valid` is ascending, and max() keeps the first max on a tie, so a tie
    # deterministically resolves to the earliest hour — not left to chance.
    peak_h = max(valid, key=lambda h: by_hour[h])  # type: ignore[arg-type, return-value]
    return {
        "by_hour": by_hour,
        "peak_hour": peak_h,
        "peak_avg_min": float(by_hour[peak_h]),  # type: ignore[arg-type]
    }


def _on_one_hour_axis(*profiles: dict | None) -> tuple[dict | None, ...]:
    """*profiles* with every ``by_hour`` as long as the longest, padded with
    None: one profile can run past midnight where another does not, and the
    weekday and weekend charts are drawn one above the other on one axis."""
    hours = max((len(p["by_hour"]) for p in profiles if p is not None), default=0)
    return tuple(
        None if p is None else {**p, "by_hour": p["by_hour"] + [None] * (hours - len(p["by_hour"]))} for p in profiles
    )


async def _daily_totals(agency_id: int, ctx: RangeCtx, conn, grain: _Grain | None) -> list[tuple[date, int, int]]:
    """``(date, samples, sum_delay_sec)`` for each date in ``ctx`` with samples, oldest first.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend``; slow path
    reads the shared grain (:func:`_fetch_grain`) so the hour-of-day filter is
    honored. Every service_type of a date pools together: the day-type split
    files dates, not services (see :mod:`pipeline.day_types`).
    """
    if ctx.time_band != "all":
        by_day = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=0)
        return [(d, n, total) for d, (n, total) in sorted(by_day.items())]
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    rows = await conn.fetch(
        "SELECT date, SUM(samples) AS samples, SUM(sum_delay_sec) AS sum_delay_sec\n"
        "FROM agg_daily_trend\n"
        # sum_delay_sec is nullable (unlike samples): a pre-backfill or
        # hand-seeded row can carry samples without it. Excluding such rows
        # keeps every figure the split reports (mean, days, samples) over one
        # row population, matching pipeline/digest/build.py's
        # _ROUTE_BASELINE_SQL.
        f"WHERE agency_id=$1{where_clause} AND sum_delay_sec IS NOT NULL AND samples > 0\n"
        "GROUP BY date\n"
        "ORDER BY date",
        agency_id,
        *params,
    )
    return [(r["date"], int(r["samples"]), int(r["sum_delay_sec"])) for r in rows]


def _avg_min(samples: int, sum_delay_sec: int) -> float:
    """Sample-weighted mean delay in minutes, 2dp half-up, from exact integer sums."""
    return float(_round2((sum_delay_sec / samples) / 60.0))


def _service_split_daily(day_types: DayTypes, totals: list[tuple[date, int, int]]) -> list[dict]:
    """Per-date avg delay, each date tagged with its day-type bucket.

    Returns ``{date: ISO str, day_type, avg_min, samples}`` rows sorted by
    date, one per date with samples. ``day_type`` is the same bucket
    :func:`_service_split` files the date under, so the modal's series match
    the card's rows. ``samples`` lets a reader tell a day collected only in
    part from a whole one.
    """
    return [
        {"date": d.isoformat(), "day_type": day_types.of(d), "avg_min": _avg_min(n, total), "samples": n}
        for d, n, total in totals
    ]


async def _movers(
    agency_id: int,
    cur_ctx: RangeCtx,
    base_ctx: RangeCtx,
    conn,
    ch=None,
    grain: _Grain | None = None,
    *,
    left_out: Mapping[str, frozenset[str]],
) -> dict:
    """Top-10 worsened + top-10 improved routes by signed delta_min, among
    the routes :func:`_listable` keeps.

    Compares ``cur_ctx`` against ``base_ctx``, both built upstream by
    ``compute_overview_summary``: the period up to its last day with data,
    and the same number of days immediately before it. Requires
    >= :data:`MOVER_MIN_SAMPLES` samples in BOTH windows for a route to enter
    the ranking — a route with a handful of obs can swing a huge delta_pct
    and would otherwise dominate top-3 with low statistical confidence.

    Frontend card variant slices :code:`.slice(0, 3)`; modal variant
    uses the full 10.
    """
    cur = await _per_route_avg(agency_id, cur_ctx, conn, ch=ch, grain=grain)
    prv = await _per_route_avg(agency_id, base_ctx, conn, ch=ch, grain=grain)
    common = set(cur) & set(prv)
    # (route_code, delta_min_2dp, delta_pct_1dp, delta_min_raw). The RAW delta
    # is carried purely to rank on: ranking on the rounded value manufactures
    # ties that don't exist (two routes at -1.7244 and -1.7203 both round to
    # -1.72, making a top-10 cutoff between them a coin-flip); ranking on the
    # raw value orders them by their actual deltas.
    deltas: list[tuple[str, float, float | None, float]] = []
    # A previous-window average below this floor makes delta_pct meaningless:
    # dividing by a near-zero baseline can turn a trivial absolute change into
    # a triple-digit-or-larger swing that reads as a real signal but isn't,
    # even past the MOVER_MIN_SAMPLES gate above (a small sample can still average
    # to a near-zero delay). The floor matches the delay-severity ramp's
    # "mild" threshold (ROUTE_BAND_MIN), which already treats anything below it as background
    # noise rather than a noticeable delay.
    MIN_PRV_AVG_FOR_PCT_MIN = 1.5
    for code in common:
        cur_avg, cur_n = cur[code]
        prv_avg, prv_n = prv[code]
        if cur_n < MOVER_MIN_SAMPLES or prv_n < MOVER_MIN_SAMPLES:
            continue
        d_min = cur_avg - prv_avg
        d_pct = round((d_min / prv_avg) * 100.0, 1) if abs(prv_avg) >= MIN_PRV_AVG_FOR_PCT_MIN else None
        deltas.append((code, float(_round2(d_min)), d_pct, d_min))
    # `(raw delta, route_code)` is a TOTAL order — route_codes are dict keys, so
    # they're distinct — which is what makes the ranking reproducible: the order
    # `common` happens to be iterated in cannot influence the result. A tie left
    # to Python's stable sort would keep the *set* iteration order, leaking
    # per-process string-hash randomization into which routes make the top-10,
    # so the same request against the same data could return different movers
    # after an app restart.
    deltas.sort(key=lambda x: (x[3], x[0]))
    deltas = _listable(deltas, left_out)
    # Partition by sign so "worse" only contains routes with positive
    # delta_min and "better" only routes with negative delta_min. With
    # a top-10 limit per list, sign-partitioning is the right way to
    # prevent the two lists from overlapping (a route can't both
    # improve and worsen at once). The partition tests the ROUNDED delta on
    # purpose: a route whose delta rounds to 0.00 belongs in neither list
    # rather than being shown as having "worsened by 0.0 min".
    worse_all = [d for d in deltas if d[1] > 0]
    better_all = [d for d in deltas if d[1] < 0]
    # Both slices resolve exact-tie order the same way — ascending route_code —
    # so `worse` re-sorts rather than reversing the ascending tail (`reversed()`
    # would have flipped ties into descending route_code for one list only).
    worse = sorted(worse_all, key=lambda x: (-x[3], x[0]))[:10]  # largest positive first
    better = better_all[:10]  # most-negative first
    codes = [c for c, _, _, _ in worse + better]
    names = await _route_short_names(agency_id, codes, conn)
    history = await _route_weekly_history(
        agency_id, codes, cur_ctx, conn, weeks_back=_MOVER_HISTORY_WEEKS, ch=ch, grain=grain
    )

    def _entry(code, dm, dp, direction):
        """Serialize one mover row (deltas, absolute averages, streak, sparkline)."""
        pts = [v for v in history.get(code, []) if v is not None]
        return {
            "route_code": code,
            "route_short_name": names.get(code),
            "delta_min": dm,
            "delta_pct": dp,
            # Absolute averages for both windows so the UI can show
            # "X min before → Y min in the period" instead of a bare Δ%.
            "current_avg_min": float(_round1(cur[code][0])),
            "previous_avg_min": float(_round1(prv[code][0])),
            "streak_weeks": _streak_weeks(history.get(code, []), direction=direction),
            "sparkline_points": pts,
        }

    return {
        "worse": [_entry(c, dm, dp, "up") for c, dm, dp, _raw in worse],
        "better": [_entry(c, dm, dp, "down") for c, dm, dp, _raw in better],
    }


# Below either floor a day type's mean is too thin to compare against the
# others: one odd day, or a handful of trips, would decide it.
SPLIT_MIN_DAYS = 3
SPLIT_MIN_SAMPLES = 1000


async def _service_day_split(
    agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None
) -> tuple[list[dict], list[dict]]:
    """``(service_split, service_split_daily)`` over ``ctx``, from one day-type
    resolution and one per-date read shared by both.

    The day types are the payload's one resolution (:func:`_calendar_from`):
    those the service filter carries when it is set, and a read over the same
    span when it is not, so the split does not change with the filter."""
    day_types = await day_types_for(ctx, conn, agency_id, from_date=_calendar_from(ctx))
    totals = await _daily_totals(agency_id, ctx, conn, grain)
    return _service_split(day_types, totals), _service_split_daily(day_types, totals)


def _service_split(day_types: DayTypes, totals: list[tuple[date, int, int]]) -> list[dict]:
    """Mean delay per day type over the dates in ``totals``.

    Each date is filed under the timetable class the agency's calendar runs
    that day (:func:`pipeline.day_types.load_day_types`), never under the
    observation's own ``service_type``: that is a raw GTFS ``service_id`` whose
    naming differs per agency (some combine a route group with the day type)
    and is empty for observations that never matched a scheduled trip, which
    can be most of them.

    Returns one ``{day_type, avg_min, days, samples, insufficient}`` per bucket
    the agency is split into, in display order: 平日 / 土曜 / 日祝, or 平日 /
    土日祝 for a feed with one weekend timetable. ``avg_min`` is the
    sample-weighted mean (None for a bucket with no data), ``days`` the dates
    with samples and ``samples`` the observation count, all over the same rows.
    ``insufficient`` marks a bucket under :data:`SPLIT_MIN_DAYS` days or
    :data:`SPLIT_MIN_SAMPLES` samples.
    """
    acc = {bucket: [0, 0, 0] for bucket in day_types.buckets}  # samples, sum_delay_sec, days
    for d, n, total in totals:
        slot = acc[day_types.of(d)]
        slot[0] += n
        slot[1] += total
        slot[2] += 1
    return [
        {
            "day_type": bucket,
            "avg_min": _avg_min(n, total) if n else None,
            "days": days,
            "samples": n,
            "insufficient": days < SPLIT_MIN_DAYS or n < SPLIT_MIN_SAMPLES,
        }
        for bucket, (n, total, days) in acc.items()
    ]


async def _daily_sparkline(
    agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None
) -> tuple[list[float], float | None]:
    """Daily avg_min points (oldest first) over ``ctx``, and the window's mean.

    Returns the FULL daily series (typically 30+ points for a 30-day
    default range) and leaves any windowing to the caller, so a consumer
    that wants a shorter tail can take one without a second query. The mean
    pools the same per-day sums and samples, so it weighs each day by its
    observations, as every other figure on the hero card does; a plain mean
    of the points would let a thin day count as much as a busy one.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend``. Slow
    path reads the shared grain (:func:`_fetch_grain`) so the hour-of-day
    filter is honored.
    """
    # (samples, total delay seconds) per day with observations: Postgres
    # numerics on the fast path, so the rounding below stays exact like the
    # SQL it replaced; plain ints on the slow path.
    days: list[tuple[int, Any]]
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT date AS day,\n"
            # sum_delay_sec is nullable (unlike samples); a row can have
            # samples set but sum_delay_sec still NULL (pre-backfill row,
            # hand-seeded fixture, or any row analyze() hasn't rewritten
            # since migration 0028) — FILTER both sides to the same row
            # population so SUM(samples) never counts a row SUM(sum_delay_sec)
            # silently dropped, matching pipeline/digest/build.py's
            # _ROUTE_BASELINE_SQL.
            "       SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric AS total,\n"
            "       SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) AS n\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY date\n"
            "ORDER BY date ASC",
            agency_id,
            *params,
        )
        days = [(r["n"], r["total"]) for r in rows if r["n"]]
    else:
        by_day = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=0)
        days = [(n, total) for _d, (n, total) in sorted(by_day.items(), key=lambda kv: kv[0]) if n]
    pts = [float(_round2(total / n / 60)) for n, total in days]
    samples = sum(n for n, _total in days)
    mean = float(_round2(sum(total for _n, total in days) / samples / 60)) if samples else None
    return pts, mean


def _whole_hour_band(band: str) -> tuple[int, int] | None:
    """``band``'s ``[start, end)`` hours on the wall clock, or None when an edge
    falls inside an hour, which ``agg_hour_daily``'s hours cannot split."""
    start, end = TIME_BAND_RANGES[band]
    if not (start.endswith(":00") and end.endswith(":00")):
        return None
    return int(start[:2]), int(end[:2])


async def _hour_band_stats(agency_id: int, days: list[date], hours: tuple[int, int], conn) -> tuple[float | None, int]:
    """``(avg_min, samples)`` over ``days`` for the departures whose scheduled
    hour falls in ``hours``, from ``agg_hour_daily``.

    The table files each departure under its hour on the service day's clock
    (24 and on after midnight), and a band is a range of the wall clock, so the
    hour is taken modulo 24, as the live band filter takes the time. The pooled
    sums are divided and rounded as the live headline divides and rounds its own
    (:func:`_headline_stats`), so a band's figure is the same from either store.
    """
    row = await conn.fetchrow(
        "SELECT SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL) AS total,\n"
        "       COALESCE(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0)::bigint AS samples\n"
        "FROM agg_hour_daily\n"
        "WHERE agency_id = $1 AND date = ANY($2::date[]) AND hour % 24 >= $3 AND hour % 24 < $4",
        agency_id,
        days,
        *hours,
    )
    samples = int(row["samples"])
    if samples == 0 or row["total"] is None:
        return None, 0
    return float(_round2((int(row["total"]) / samples) / 60.0)), samples


async def _reference(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> dict:
    """The headline's figure over the reference window ``ctx``
    (:func:`_reference_ctx`), and whether it may stand as the agency's usual
    level: ``reason`` names why not, and the figure is then withheld.

    The figure is the headline's own statistic under the headline's own scope.
    With every hour it is :func:`_headline_stats` itself, over
    ``agg_daily_trend``. Under a time band it reads ``agg_hour_daily``
    (:func:`_hour_band_stats`), which pools every route, so a time band with a
    routes filter has no aggregate to read and is ``not_aggregated``: a live
    scan of ``REFERENCE_DAYS`` days would cost several times the headline's own.
    That is decided before anything is read, and its counts are then 0.

    A service filter is resolved over this window alone, so the period's own
    day types stay those of the span the rest of the payload reads. Coverage and
    the reasons a figure is withheld are the shared rules of
    :mod:`pipeline.reports.reference`.
    """
    hours = _whole_hour_band(ctx.time_band) if ctx.time_band != "all" and not ctx.routes else None
    if ctx.time_band != "all" and hours is None:
        return {"avg_min": None, **reference_window(ctx, Coverage(), "not_aggregated")}

    ctx = await resolve_service_dates(ctx, conn, agency_id)
    selected, coverage = await reference_coverage(agency_id, ctx, conn)

    avg_min: float | None = None
    samples = 0
    if hours is None:
        avg_min, samples = await _headline_stats(agency_id, ctx, conn)
    elif selected:
        avg_min, samples = await _hour_band_stats(agency_id, selected, hours, conn)

    reason = reference_reason(samples, coverage)
    return {"avg_min": avg_min if reason is None else None, **reference_window(ctx, coverage, reason, samples)}


def _change(avg_min: float | None, against: float | None) -> tuple[float | None, float | None]:
    """``(delta_min, delta_pct)`` of ``avg_min`` from ``against``; the share is
    None against 0 min, and both are None without both figures."""
    if avg_min is None or against is None:
        return None, None
    delta_min = float(_round2(avg_min - against))
    return delta_min, (round((delta_min / against) * 100.0, 1) if against != 0 else None)


@perf.timed("reports.overview")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def compute_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    *,
    pool=None,
    ch=None,
) -> dict:
    """Build the 概況 payload for one agency over ``ctx``.

    The headline covers the scope period itself (``cur_ctx``: ``ctx`` up to
    its last day with data), so the hero row and the scope sentence describe
    the same period, and compares it against a window of the SAME WIDTH
    immediately before it (``base_ctx``). Equal widths keep the "vs. the
    period before it" copy honest: two windows of different lengths differ in
    day-of-week composition, which alone can manufacture a delta. The movers
    and the routes-to-check list rank over the same two windows;
    concentration / peak / service_split / sparkline aggregate over the full
    ctx.

    The headline also carries its ``reference``: the same figure over the
    ``REFERENCE_DAYS`` days before the period under the same scope, the
    agency's own usual level, or the reason it has none (:func:`_reference`).
    The baseline answers "did it change since just before?"; the reference
    answers "is this usual for the agency?".

    When ``pool`` is supplied (non-None), the stage queries are dispatched
    as concurrent asyncio tasks, each acquiring its own pooled connection,
    with at most ``OVERVIEW_FANOUT_LIMIT`` holding one at a time. ``conn`` is
    then unused and may be None: no task waits for a connection while one is
    held for it, so concurrent requests cannot starve each other of the
    pool and deadlock.  The three
    ``_peak_hour_by_dow`` calls dominate a cold load, so they start first
    and the rest share the remaining slots.  When
    ``pool`` is None (the default) the stages run sequentially on ``conn``,
    each in its own timed_block, for tests and ad-hoc callers.

    ``ch`` is the ClickHouse client used whenever live rows are needed
    (``updates`` lives in ClickHouse, not Postgres). Defaults to ``None`` for
    callers that only ever exercise the full fast path (``time_band == 'all'``
    and no routes filter; most existing tests); real dispatch (the
    ``/overview/summary`` route) always passes the real client.

    On the slow path (``ctx.time_band != 'all'``) the shared grain is read up
    front and handed to every stage helper, which then derives its own numbers
    from it in Python. It comes from the band aggregates
    (:func:`_fetch_band_aggregate`) whenever they answer the band
    (:func:`_band_aggregate_answers`) and analyze has built them for the agency
    (:func:`_band_aggregate_built`), with no ClickHouse read but the hour
    profiles' under a routes filter, which degrade to null as under a routes
    filter alone. Otherwise the shared ClickHouse query (:func:`_fetch_grain`)
    serves it, as the rest of this docstring describes. That query is the
    only query for any ``ctx`` wide enough for the grain to also cover
    :func:`_route_weekly_history`'s 4-week sparkline span (every default 30-day
    request); a narrower ``ctx`` costs that one helper a second scan. Under a
    routes filter alone the grain is fetched only for the three hour profiles,
    whose ``agg_hour_daily`` is aggregated across routes; every other helper
    still reads its ``agg_*`` table, and a failed fetch degrades just those
    three fields to null. With neither filter ``grain`` stays ``None`` and
    every helper reads its ``agg_*`` table.

    Under a time band, a ClickHouse grain past the client's row cap or its
    time budget is fetched again over the period's route rows alone
    (:func:`_fetch_grain`'s ``"period_routes"``), and every figure that needs
    the window before the period or the hour set is null: the baseline and
    the headline's deltas, the movers, each listed route's ``delta_min`` and
    the three hour profiles. Any other ClickHouse failure, or the retry
    failing too, still fails the payload: every figure reads the grain. A
    degraded grain costs about twice ``get_ch_client``'s
    ``max_execution_time`` (ClickHouse checks the limit periodically, so a
    query can run slightly past it), and nothing else in a degraded payload
    reads ClickHouse (the movers' weekly scan needs the baseline), so the
    request ends inside the frontend's ``REQUEST_TIMEOUT_MS``
    (``frontend/src/api/client.ts``) as long as that timeout stays above twice
    the ClickHouse budget plus the Postgres stages. A payload a timeout
    degraded is cached only briefly (:func:`pipeline.cache.cache_briefly`);
    one the row cap degraded keeps the full TTL, since the same request
    passes the cap again.
    """
    if ctx.service != "all":
        # Every window derived below starts at or after _calendar_from(ctx).
        if pool is None:
            ctx = await resolve_service_dates(ctx, conn, agency_id, from_date=_calendar_from(ctx))
        else:
            async with pool.acquire() as c:
                ctx = await resolve_service_dates(ctx, c, agency_id, from_date=_calendar_from(ctx))

    grain: _Grain | None = None
    # Under a routes filter alone only the three hour profiles need live rows
    # (agg_hour_daily is aggregated across routes; every other stage reads its
    # agg_* table), so the grain is fetched for them, and a ClickHouse failure
    # degrades just those three fields to null rather than failing the payload.
    peaks_unavailable = False
    baseline_unavailable = False
    # The grain's Postgres reads borrow from the pool per statement when the
    # stages hold their own connections.
    grain_conn = conn if pool is None else PooledConnection(pool)

    async def _period_hours() -> _Grain | None:
        """The period's hour set from ClickHouse, or None once a failure has
        degraded the three hour profiles: no other figure reads it."""
        try:
            return await _fetch_grain(agency_id, ctx, grain_conn, ch, shape="period_hours")
        except Exception as exc:
            _log.warning(
                "ClickHouse grain fetch failed for agency %s — degrading the peak-hour profiles to null",
                agency_id,
                exc_info=True,
            )
            if not is_result_overflow(exc):
                cache_briefly()
            return None

    band_hours = _whole_hour_band(ctx.time_band) if ctx.time_band != "all" else None
    if band_hours is not None and _band_aggregate_answers(ctx):
        async with perf.timed_block("overview.band_aggregate"):
            grain = await _fetch_band_aggregate(agency_id, ctx, grain_conn, band_hours)
            if not grain.route_rows and not await _band_aggregate_built(agency_id, grain_conn):
                grain = None
        if grain is not None and ctx.routes:
            if ch is None:
                raise RuntimeError("overview hour profiles under a routes filter require a ClickHouse client")
            async with perf.timed_block("overview.grain"):
                hours = await _period_hours()
            grain = replace(grain, hour_rows=None if hours is None else hours.hour_rows)
            peaks_unavailable = hours is None
    if grain is None and (ctx.time_band != "all" or ctx.routes):
        if ch is None:
            raise RuntimeError("overview slow path requires a ClickHouse client")
        async with perf.timed_block("overview.grain"):
            if ctx.time_band != "all":
                try:
                    grain = await _fetch_grain(agency_id, ctx, grain_conn, ch)
                except Exception as exc:
                    if not (is_result_overflow(exc) or is_query_timeout(exc)):
                        raise
                    _log.warning(
                        "Overview grain for agency %s %s — serving the period's route rows alone, "
                        "without the baseline or the peak-hour profiles",
                        agency_id,
                        "passed the ClickHouse row cap" if is_result_overflow(exc) else "ran out of ClickHouse time",
                    )
                    grain = await _fetch_grain(agency_id, ctx, grain_conn, ch, shape="period_routes")
                    peaks_unavailable = baseline_unavailable = True
                    if is_query_timeout(exc):
                        cache_briefly()
            else:
                grain = await _period_hours()
                peaks_unavailable = grain is None

    # The route lists leave out one set of codes, judged once over the period,
    # so every list leaves out the same codes whichever window it ranks over.
    async with perf.timed_block("overview.latest_date"):
        if pool is None:
            latest = await _latest_data_date(agency_id, ctx, conn, ch=ch, grain=grain)
            left_out = await left_out_unless_named(conn, agency_id, ctx)
        else:
            async with pool.acquire() as c:
                latest = await _latest_data_date(agency_id, ctx, c, ch=ch, grain=grain)
                left_out = await left_out_unless_named(c, agency_id, ctx)
    # If no data anywhere in ctx, anchor to ctx.to_date so empty payload
    # still has a sensible window_to.
    anchor = latest if latest is not None else ctx.to_date

    # The current window is the scope period up to its last day with data, so
    # the headline describes the period the scope sentence names.
    cur_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=anchor,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
        service_dates=ctx.service_dates,
    )
    # base_ctx is the SAME WIDTH as cur_ctx, immediately preceding it (see the
    # docstring). It lies wholly before ctx, which is why the day types and,
    # under a time band, the grain reach back (:func:`_calendar_from`,
    # :func:`_grain_lookback_days`).
    base_to = cur_ctx.from_date - timedelta(days=1)
    base_from = base_to - timedelta(days=cur_ctx.days - 1)
    base_ctx = RangeCtx(
        from_date=base_from,
        to_date=base_to,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
        service_dates=ctx.service_dates,
    )
    # None when the grain was fetched over the period alone, without the days before it.
    baseline_ctx = None if baseline_unavailable else base_ctx
    # The reference reads Postgres alone, so a degraded grain leaves it standing.
    ref_ctx = _reference_ctx(cur_ctx)

    movers: dict | None
    if pool is None:
        async with perf.timed_block("overview.headline"):
            avg_min, samples = await _headline_stats(agency_id, cur_ctx, conn, ch=ch, grain=grain)
            baseline_avg = (
                (await _headline_stats(agency_id, baseline_ctx, conn, ch=ch, grain=grain))[0]
                if baseline_ctx is not None
                else None
            )
            reference = await _reference(agency_id, ref_ctx, conn)

        async with perf.timed_block("overview.movers"):
            movers = (
                await _movers(agency_id, cur_ctx, baseline_ctx, conn, ch=ch, grain=grain, left_out=left_out)
                if baseline_ctx is not None
                else None
            )
        async with perf.timed_block("overview.concentration"):
            concentration = await _concentration(agency_id, ctx, conn, ch=ch, grain=grain, left_out=left_out)
        async with perf.timed_block("overview.top_delayed"):
            top_delayed = await _top_delayed_routes(
                agency_id, cur_ctx, conn, ch=ch, grain=grain, left_out=left_out, base_ctx=baseline_ctx
            )
        async with perf.timed_block("overview.peaks"):
            peak, peak_weekday, peak_weekend = (
                (None, None, None)
                if peaks_unavailable
                else (
                    await _peak_hour_by_dow(agency_id, ctx, conn, ctx.dow, ch=ch, grain=grain),
                    await _peak_hour_by_dow(agency_id, ctx, conn, "weekday", ch=ch, grain=grain),
                    await _peak_hour_by_dow(agency_id, ctx, conn, "weekend", ch=ch, grain=grain),
                )
            )
        async with perf.timed_block("overview.service_split"):
            service_split, service_split_daily = await _service_day_split(agency_id, ctx, conn, ch=ch, grain=grain)
        async with perf.timed_block("overview.sparkline"):
            sparkline_points, period_avg = await _daily_sparkline(agency_id, ctx, conn, ch=ch, grain=grain)

    else:
        # Pool-gather path — each task acquires its own pooled connection
        # so the queries can run concurrently. A single asyncpg
        # connection cannot multiplex queries; pool.acquire() queues when
        # saturated, so a per-request `asyncio.Semaphore(OVERVIEW_FANOUT_LIMIT)`
        # caps how many of the stages hold a connection at once, so two
        # concurrent overview requests cannot drain the pool between them. `ch`
        # (a single shared ClickHouse client, not pool-backed) is closed
        # over directly rather than threaded through `_own_conn`'s *args.
        # No per-stage timed_blocks here; the top-level reports.overview
        # label captures the wall-clock total. Whenever a stage needs live rows
        # the shared ClickHouse grain query has already run (above, before this
        # branch), so what still fans out here is mostly Postgres work —
        # `_route_short_names` and every fast-path helper's `agg_*` read (plus
        # `_route_weekly_history`'s live scan, in the narrow-`ctx` case where
        # the grain can't cover its span).
        fanout = asyncio.Semaphore(OVERVIEW_FANOUT_LIMIT)

        async def _own_conn(fn, *args, **kwargs):
            """Under the fan-out bound, acquire a connection and call
            ``fn(*args, conn, ch=ch, grain=grain, **kwargs)``."""
            async with fanout, pool.acquire() as c:
                return await fn(*args, c, ch=ch, grain=grain, **kwargs)

        async def _with_baseline(fn, *args, **kwargs):
            """``_own_conn(fn, *args, **kwargs)``, or None without a baseline."""
            if baseline_ctx is None:
                return None
            return await _own_conn(fn, *args, **kwargs)

        async def _peak_dow(group: str) -> dict | None:
            """``_peak_hour_by_dow`` for ``group``, through the same bounded acquire."""
            if peaks_unavailable:
                return None
            async with fanout, pool.acquire() as c:
                return await _peak_hour_by_dow(agency_id, ctx, c, group, ch=ch, grain=grain)

        # Stages take fan-out slots in argument order, so the hour profiles,
        # the slowest reads on the fast path, go first rather than queueing
        # behind stages that finish quickly.
        (
            peak,
            peak_weekday,
            peak_weekend,
            (avg_min, samples),
            baseline_stats,
            reference,
            movers,
            concentration,
            top_delayed,
            (service_split, service_split_daily),
            (sparkline_points, period_avg),
        ) = await asyncio.gather(
            _peak_dow(ctx.dow),
            _peak_dow("weekday"),
            _peak_dow("weekend"),
            _own_conn(_headline_stats, agency_id, cur_ctx),
            _with_baseline(_headline_stats, agency_id, baseline_ctx),
            _own_conn(_reference, agency_id, ref_ctx),
            _with_baseline(_movers, agency_id, cur_ctx, baseline_ctx, left_out=left_out),
            _own_conn(_concentration, agency_id, ctx, left_out=left_out),
            _own_conn(_top_delayed_routes, agency_id, cur_ctx, left_out=left_out, base_ctx=baseline_ctx),
            _own_conn(_service_day_split, agency_id, ctx),
            _own_conn(_daily_sparkline, agency_id, ctx),
        )
        baseline_avg = baseline_stats[0] if baseline_stats is not None else None

    delta_min, delta_pct = _change(avg_min, baseline_avg)
    reference_delta_min, reference_delta_pct = _change(avg_min, reference["avg_min"])
    peak, peak_weekday, peak_weekend = _on_one_hour_axis(peak, peak_weekday, peak_weekend)
    return {
        "headline": {
            "avg_min": avg_min,
            "baseline_avg_min": baseline_avg,
            "delta_min": delta_min,
            "delta_pct": delta_pct,
            "samples": samples,
            "window_from": cur_ctx.from_date.isoformat(),
            "window_to": cur_ctx.to_date.isoformat(),
            "reference": {**reference, "delta_min": reference_delta_min, "delta_pct": reference_delta_pct},
        },
        "movers": movers,
        "concentration": concentration,
        "top_delayed": top_delayed,
        "peak_hour": peak,
        "peak_hour_weekday": peak_weekday,
        "peak_hour_weekend": peak_weekend,
        "service_split": service_split,
        "service_split_daily": service_split_daily,
        "sparkline_points": sparkline_points,
        "period_avg_min": period_avg,
        # The window the period-wide figures count, after the router's clamp,
        # so a client labels them by the days counted rather than its own URL.
        "period_from": ctx.from_date.isoformat(),
        "period_to": ctx.to_date.isoformat(),
    }
