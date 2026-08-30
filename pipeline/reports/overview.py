"""The 概況 magazine payload and its private stage-query helpers.

Public surface
--------------
compute_overview_summary(agency_id, ctx, conn, locale, *, pool=None) -> dict
    Build the full Overview tab payload.  Pass ``pool`` to run the ten
    stage queries concurrently (each task acquires its own connection);
    omit it (or pass ``None``) for the sequential single-connection path
    used by tests and ad-hoc callers.

Fast vs slow paths
------------------
Most helpers have two internal branches:

* **Fast path** (``ctx.time_band == "all"``) — reads the pre-aggregated
  ``agg_daily_trend`` / ``agg_route_hour`` tables, sub-millisecond even
  over multi-month ranges.
* **Slow path** (any other time_band) — falls back to the live ``updates``
  table (ClickHouse, via ``pipeline.reports.filters._dedup_cte_ch``) so the
  hour-of-day filter is honoured.

``_peak_hour_by_dow`` reads the per-day ``agg_hour_daily`` (filtering dates by
DOW) on the fast path; ``agg_route_hour`` can't serve it (no date column). It
falls back to the live path under a ``service``/``routes`` filter, since that
table aggregates across all routes/services.

The shared slow-path grain
--------------------------
Every slow-path helper used to issue its OWN ``_dedup_cte_ch`` scan of
``updates``, so one Overview request fanned out ~12 independent full dedup
scans of *substantially the same rows* — each one reading a large fraction
of an agency's history over even a 30-day window, which put several of them
over ``api.clickhouse.get_ch_client``'s 30 s ``max_execution_time`` cap (a
real 500). They now share ONE round trip: :func:`_fetch_grain` pre-aggregates the
dedup output to ``(date, route_code, service_type, hour)`` once, and each
helper derives its own answer from that grain in Python. See
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
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Iterator

from api.range import RangeCtx
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.reports.filters import _agg_filter, _ch_rows, _dedup_cte_ch, _round2, _time_band_sql_on

_log = logging.getLogger(__name__)

# How many days before ``ctx.from_date`` the shared grain has to reach.
# ``compute_overview_summary`` builds its two comparison windows inline as
# ``cur_from = max(anchor - 6, ctx.from_date)`` and ``base_from = cur_from - 7``,
# so the earliest date any consumer can ask for is ``ctx.from_date - 7`` — the
# ``max(..., ctx.from_date)`` clamp on ``cur_from`` is what makes 7 the exact
# worst case rather than a heuristic. See :func:`_fetch_grain`, and note that
# :func:`_grain_window` enforces the resulting bound at runtime.
_GRAIN_LOOKBACK_DAYS = 7


# ---------------------------------------------------------------------------
# Shared slow-path grain
# ---------------------------------------------------------------------------

# (date, route_code, service_type, hour, samples, sum_delay_sec, sum_late_sec).
# ``service_type`` is Nullable in ClickHouse; ``hour`` is None only for rows
# with a NULL ``scheduled_time`` (which the time-band filter already excludes —
# see _fetch_grain), so consumers that need an hour skip None defensively.
# ``sum_delay_sec`` / ``sum_late_sec`` stay in SECONDS as exact integers, so
# summing partial groups in Python is lossless and the single trailing
# ``/ 60.0`` reproduces the SQL's own ``/ 60.0`` bit-for-bit.
_GrainRow = tuple[date, str, "str | None", "int | None", int, int, int]


@dataclass(frozen=True)
class _Grain:
    """One request's worth of deduped ``updates``, fetched in ONE round trip.

    ``rows`` is the dedup output pre-aggregated to
    ``(date, route_code, service_type, hour)`` over ``[from_date, to_date]``
    with ``agency_id`` + ``service`` + ``routes`` + ``time_band`` already
    applied, but deliberately WITHOUT any day-of-week filter — see
    :func:`_fetch_grain`.
    """

    rows: tuple[_GrainRow, ...]
    from_date: date
    to_date: date


def _require_grain(grain: _Grain | None) -> _Grain:
    """Narrow an optional grain on a slow-path branch.

    Every helper below is module-private and only ever reached through
    :func:`compute_overview_summary`, which prefetches the grain whenever
    ``ctx.time_band != 'all'`` — i.e. exactly when a slow-path branch can be
    taken. An explicit raise rather than ``assert`` so the invariant still
    holds under ``python -O``.
    """
    if grain is None:  # pragma: no cover - unreachable via the public entry point
        raise RuntimeError("overview slow path reached without a prefetched grain")
    return grain


def _dow_matches(d: date, dow: str) -> bool:
    """Python counterpart of :func:`api.range.dow_clause_ch`.

    ``dow_clause_ch`` filters on ``toDayOfWeek(toDate(captured_at,
    'Asia/Tokyo'))`` — mode 0, so 1=Monday..7=Sunday — and the grain's ``date``
    column IS that same ``toDate(captured_at, 'Asia/Tokyo')`` expression, so
    ``date.isoweekday()`` (also 1=Monday..7=Sunday) reproduces it exactly.
    """
    if dow == "all":
        return True
    iso = d.isoweekday()
    if dow == "weekday":
        return 1 <= iso <= 5
    return iso in (6, 7)


def _grain_covers(grain: _Grain | None, from_date: date, to_date: date) -> bool:
    """Whether ``grain`` actually spans ``[from_date, to_date]``.

    The non-raising counterpart of :func:`_check_grain_covers`, for the one
    consumer whose window is a legitimate *maybe*: :func:`_route_weekly_history`
    asks for ``weeks_back * 7`` days ending at ``ctx.to_date``, which the grain
    contains for a wide ``ctx`` (every default 30-day request) and genuinely
    does not for a narrow one — so "not covered" there means "fall back to the
    live scan", not "invariant violated". Every other consumer's window is
    derived from ``ctx`` by construction and MUST be covered, which is why
    :func:`_check_grain_covers` raises for them.
    """
    return grain is not None and from_date >= grain.from_date and to_date <= grain.to_date


def _check_grain_covers(grain: _Grain, from_date: date, to_date: date) -> None:
    """Fail loudly if a consumer asks for a window the grain never fetched.

    The grain spans ``[ctx.from_date - _GRAIN_LOOKBACK_DAYS, ctx.to_date]``,
    which is derived from how :func:`compute_overview_summary` builds
    ``cur_ctx`` / ``base_ctx``. If that construction ever drifts — an off-by-one
    in the clamp, a wider baseline — the Python filters here would happily
    aggregate over a TRUNCATED window and return a plausible-looking but wrong
    user-facing average, with nothing to notice. Raising turns that class of
    regression into a visible failure instead of a quiet data bug.
    """
    if not _grain_covers(grain, from_date, to_date):
        raise RuntimeError(
            f"overview grain [{grain.from_date}..{grain.to_date}] does not cover "
            f"the requested window [{from_date}..{to_date}]"
        )


def _grain_window(grain: _Grain, ctx: RangeCtx) -> Iterator[_GrainRow]:
    """Grain rows inside ``ctx``'s date window that satisfy ``ctx.dow``.

    This is the in-Python equivalent of the date-range + DOW half of
    ``build_updates_filter_ch(ctx)``; the other half (service / routes /
    time_band) was already applied server-side when the grain was fetched,
    because those three are identical across every consumer of one request.

    Not a generator function, so the coverage check fires when this is CALLED
    rather than when the result is first iterated.
    """
    _check_grain_covers(grain, ctx.from_date, ctx.to_date)
    lo, hi, dow = ctx.from_date, ctx.to_date, ctx.dow
    return (row for row in grain.rows if lo <= row[0] <= hi and _dow_matches(row[0], dow))


def _grain_avg_min(rows: Iterable[_GrainRow]) -> tuple[float | None, int]:
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
        n += row[4]
        total += row[5]
    if n == 0:
        return None, 0
    return (total / n) / 60.0, n


def _grain_sum_by(rows: Iterable[_GrainRow], key_index: int, value_index: int = 5) -> dict:
    """``{group_key: (samples, summed_value_sec)}`` for one grain column.

    ``value_index`` picks ``sum_delay_sec`` (5, the default — for averages) or
    ``sum_late_sec`` (6 — for :func:`_concentration`'s lateness contribution).
    """
    acc: dict = {}
    for row in rows:
        slot = acc.get(row[key_index])
        if slot is None:
            acc[row[key_index]] = [row[4], row[value_index]]
        else:
            slot[0] += row[4]
            slot[1] += row[value_index]
    return {k: (v[0], v[1]) for k, v in acc.items()}


async def _fetch_grain(agency_id: int, ctx: RangeCtx, ch) -> _Grain:
    """Fetch the whole request's deduped ``updates`` in ONE ClickHouse query.

    Replaces the ~12 independent ``_dedup_cte_ch`` scans the slow-path stage
    helpers used to issue (see the module docstring). Three things make a
    single shared scan possible:

    * ``service``, ``routes`` and ``time_band`` are IDENTICAL across every
      consumer within one ``compute_overview_summary`` call — ``cur_ctx``,
      ``base_ctx`` and ``_peak_hour_by_dow``'s override all copy them verbatim
      from ``ctx`` — so they are pushed into the shared server-side WHERE.
    * ``dow`` is NOT: ``_peak_hour_by_dow`` deliberately REPLACES ``ctx.dow``
      with ``weekday``/``weekend`` regardless of what the user chose. So no DOW
      filter is applied here at all; each consumer applies its own in Python
      off each row's ``date`` (:func:`_dow_matches`).
    * The date windows differ (``ctx`` / ``cur_ctx`` / ``base_ctx``), so the
      grain spans their union and each consumer re-filters. The union bound is
      ``[ctx.from_date - 7, ctx.to_date]``:
      ``cur_ctx.from_date = max(anchor - 6, ctx.from_date) >= ctx.from_date``
      and ``base_ctx.from_date = cur_ctx.from_date - 7``, so no consumer ever
      reaches further back than 7 days before ``ctx.from_date``; and
      ``anchor`` is either a date inside ``ctx`` or ``ctx.to_date`` itself, so
      ``cur_ctx.to_date = anchor <= ctx.to_date`` bounds the far end. Because
      this bound needs no knowledge of ``anchor``, ``_latest_data_date`` —
      which computes ``anchor`` — can be served from the grain too, instead of
      costing its own round trip.

    One consumer, :func:`_route_weekly_history`, asks for a window this bound
    does NOT guarantee: ``weeks_back * 7`` days back from ``cur_ctx.to_date``,
    which for the default ``weeks_back=4`` reaches ``anchor - 27``. The grain
    covers that whenever ``anchor - ctx.from_date >= 20`` — true for a default
    30-day request, false for a narrow one — so that helper checks coverage with
    :func:`_grain_covers` and keeps its own live scan for the uncovered case.
    Widening the grain to always cover it was rejected: it would add ~3 weeks of
    scan to EVERY slow-path request to save a scan on the minority of requests
    that are too narrow to be covered.

    ``hour`` is grouped alongside ``route_code``/``service_type`` rather than
    fetched separately for ``_peak_hour_by_dow``: adding it to the existing
    group-by costs nothing extra (the cost is all in the scan, not the
    group-by cardinality), whereas a second query genuinely doubles it. Even
    a full-365-day window stays under ``get_ch_client``'s 200 k-row
    ``max_result_rows`` cap, though not with so much headroom that adding
    another group-by dimension or widening the window further is free to do
    without rechecking against the cap.

    The hour is read off ``scheduled_time``'s first two characters, exactly as
    ``_peak_hour_by_dow``'s live path did: it is a zero-padded ``'HH:MM[:SS]'``
    ``Nullable(String)`` in ClickHouse, not a native TIME column (see
    ``api.range.time_band_clause_ch``'s docstring). A NULL ``scheduled_time``
    yields a NULL hour, but cannot actually occur here — ``time_band != 'all'``
    is what puts us on the slow path at all, and ``time_band_clause_ch``'s
    ``substring(scheduled_time, 1, 5) >= ...`` comparison is already NULL for
    those rows and so drops them. ``toUInt8OrNull`` rather than the live path's
    bare ``toUInt8``: identical on every real row (all four ingested agencies
    write a zero-padded 2-digit hour — verified, zero non-matching rows), but a
    hypothetical malformed value degrades to "excluded from the hour histogram"
    instead of failing the one query the whole payload now depends on.
    """
    span_ctx = RangeCtx(
        from_date=ctx.from_date - timedelta(days=_GRAIN_LOOKBACK_DAYS),
        to_date=ctx.to_date,
        dow="all",  # applied per-consumer in Python; see the docstring
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )
    cte_sql, ch_params = _dedup_cte_ch(span_ctx)
    hour_expr = "toUInt8OrNull(substring(scheduled_time, 1, 2))"
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT date,\n"
        "       route_code,\n"
        "       service_type,\n"
        f"       {hour_expr} AS hour,\n"
        "       count(*) AS samples,\n"
        "       sum(dep_delay) AS sum_delay,\n"
        "       sum(greatest(dep_delay, 0)) AS sum_late\n"
        "FROM deduped\n"
        f"GROUP BY date, route_code, service_type, {hour_expr}",
        parameters={"agency_id": agency_id, **ch_params},
    )
    rows: tuple[_GrainRow, ...] = tuple(
        (d, route_code, service_type, hour, int(samples), int(sum_delay or 0), int(sum_late or 0))
        for d, route_code, service_type, hour, samples, sum_delay, sum_late in result.result_rows
    )
    return _Grain(rows=rows, from_date=span_ctx.from_date, to_date=span_ctx.to_date)


async def _latest_data_date(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> date | None:
    """Most recent date inside ctx that has any samples.

    Used to anchor the headline's 7-day window to where data actually
    exists. Keeps the "this week vs last week" semantics meaningful
    when ingest is lagging or the user selects a wide historical range.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` for
    sub-millisecond response. Slow path (any other time band) reads the shared
    grain (:func:`_fetch_grain`), which honours the same filters — it replaces
    a ``maxOrNull(date) FROM deduped`` scan of live ``updates``.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = f"SELECT MAX(date::date) AS d FROM agg_daily_trend WHERE agency_id=$1{where_clause}"
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
            "SELECT CASE WHEN SUM(samples) > 0\n"
            "            THEN ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2)\n"
            "            ELSE NULL END AS avg_min,\n"
            "       COALESCE(SUM(samples), 0)::int AS samples\n"
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
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min,\n"
            "       SUM(samples)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "HAVING SUM(samples) > 0 AND SUM(avg_min * samples) IS NOT NULL"
        )
        rows = await conn.fetch(sql, agency_id, *params)
        return {r["route_code"]: (float(r["avg_min"]), int(r["samples"])) for r in rows}

    # Grouping by route_code means every emitted group has >= 1 sample, so
    # unlike _headline_stats there's no empty-input case to guard here. Values
    # stay UNROUNDED: _movers derives its deltas from them and does its own
    # rounding.
    #
    # `route_code is not None` guard: ClickHouse's `route_code` is Nullable
    # (both ingest strategies can produce a row with no resolvable route —
    # see db/clickhouse/schema.sql), so a NULL-route group is reachable here.
    # `_movers` feeds this dict's keys straight into `_route_weekly_history`'s
    # `route_code IN {rw_route_codes:Array(String)}` parameter — a `None`
    # element there isn't valid `Array(String)` and raises a ClickHouse
    # DatabaseError, 500ing the whole /overview/summary request. There's no
    # meaningful "route" to attribute a NULL-route mover to anyway.
    by_route = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=1)
    return {
        route_code: ((total / n) / 60.0, n) for route_code, (n, total) in by_route.items() if route_code is not None
    }


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
    (ClickHouse) scan when it doesn't. The grain spans
    ``[ctx.from_date - _GRAIN_LOOKBACK_DAYS, ctx.to_date]`` while this function
    needs ``[ctx.to_date - (7 * weeks_back - 1), ctx.to_date]``, so for the
    default ``weeks_back=4`` the grain covers it whenever ``ctx`` is at least
    ~21 days wide — i.e. every default 30-day request, which is the common
    case. That matters because this was the second ClickHouse round trip of an
    otherwise one-round-trip slow path, on top of the grain's own scan cost,
    and it fires whenever ``_movers`` has any candidate routes at all.

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
            )
            where, params, n = _agg_filter(window_ctx, next_param=2)
            where_clause = f" AND ({where})" if where else ""
            rows = await conn.fetch(
                "SELECT route_code,\n"
                "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
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
        for row in _require_grain(grain).rows:
            d, route_code = row[0], row[1]
            if route_code not in wanted or not (span_from <= d <= ctx.to_date):
                continue
            if not _dow_matches(d, ctx.dow):
                continue
            slot = acc.setdefault((route_code, (ctx.to_date - d).days // 7), [0, 0])
            slot[0] += row[4]
            slot[1] += row[5]
        for code in route_codes:
            for k in range(weeks_back - 1, -1, -1):
                bucket = acc.get((code, k))
                out[code].append(((bucket[1] / bucket[0]) / 60.0) if bucket else None)
        return out

    # Live ClickHouse path (reached only when the grain doesn't reach back far
    # enough — a `ctx` window narrower than ~3 weeks): one dedup CTE over the
    # whole [ctx.to_date - weeks_back*7 + 1, ctx.to_date] span, bucketed into
    # weeks_back week-index groups via `intDiv(dateDiff('day', date, to_date),
    # 7)` — 0 is the most recent 7-day bucket ending at ctx.to_date,
    # weeks_back-1 the oldest, matching the original per-week loop's `k` and its
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
    )
    cte_sql, ch_params = _dedup_cte_ch(span_ctx)
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


async def _concentration(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> dict:
    """Top-20 routes by total positive delay contribution + rest share.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` and
    approximates ``SUM(GREATEST(dep_delay, 0))`` as
    ``SUM(GREATEST(avg_min, 0) * samples)`` per route, in minutes. Routes
    that ran early on net (negative ``avg_min``) contribute zero — same
    intent as the per-row metric: contribution to LATENESS, not the
    signed sum.

    Slow path (any non-default time band) reads the shared grain
    (:func:`_fetch_grain`), whose ``sum_late_sec`` column is the per-row
    ``SUM(GREATEST(dep_delay, 0))`` computed exactly, so the hour-of-day filter
    is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT route_code,\n"
            "       SUM(GREATEST(avg_min, 0) * samples)::float AS total_late_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "ORDER BY total_late_min DESC NULLS LAST, route_code",
            agency_id,
            *params,
        )
    else:
        # Grouping by route_code means every emitted group has >= 1 sample (no
        # empty-input case), and the summed lateness is never NULL for a
        # non-empty group, so no ORDER BY ... NULLS LAST equivalent is needed.
        # Ties are broken by route_code so the top-20 cut is reproducible —
        # the ClickHouse `ORDER BY total_late_min DESC` this replaces left tied
        # rows in an arbitrary, run-to-run-unstable order.
        by_route = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=1, value_index=6)
        rows = [
            {"route_code": code, "total_late_min": late / 60.0}
            for code, (_n, late) in sorted(by_route.items(), key=lambda kv: (-kv[1][1], kv[0]))
        ]
    if not rows:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    grand_total = sum(float(r["total_late_min"] or 0.0) for r in rows)
    if grand_total == 0:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    top_n = rows[:20]
    codes = [r["route_code"] for r in top_n]
    names = await _route_short_names(agency_id, codes, conn)
    top_n_sum = sum(float(r["total_late_min"] or 0.0) for r in top_n)
    return {
        "top_routes": [
            {
                "route_code": r["route_code"],
                "route_short_name": names.get(r["route_code"]),
                "share_pct": round((float(r["total_late_min"] or 0.0) / grand_total) * 100.0, 1),
            }
            for r in top_n
        ],
        "rest_share_pct": round(((grand_total - top_n_sum) / grand_total) * 100.0, 1),
        "rest_route_count": max(len(rows) - len(top_n), 0),
    }


async def _top_delayed_routes(
    agency_id: int, cur_ctx: RangeCtx, conn, limit: int = 5, ch=None, grain: _Grain | None = None
) -> dict:
    """Routes ranked by absolute current-window avg delay ("routes to check
    now"), plus a count of routes at/above the DELAY_RAMP "not ok" threshold
    (2.0 min — frontend/src/styles/tokens.ts's ok/mild boundary).

    Uses cur_ctx (the same last-7-days-of-ctx window compute_overview_summary
    already builds for the headline), not the full ctx, so the KPI row's
    three stats and the routes list all describe the same snapshot.

    Fast path mirrors _concentration()'s: reads agg_daily_trend, but computes
    each route's true weighted average (SUM(avg_min*samples)/SUM(samples)),
    not _concentration()'s "total lateness contribution" sum — a route with
    few samples but a high average must outrank a route with more samples
    but a lower average, which _concentration()'s metric would get backwards.
    Slow path reads the shared grain (:func:`_fetch_grain`) for a non-default
    time_band, same as _concentration().
    """
    if cur_ctx.time_band == "all":
        where, params, _ = _agg_filter(cur_ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT route_code,\n"
            "       SUM(avg_min * samples)::float / NULLIF(SUM(samples), 0) AS avg_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "HAVING SUM(samples) > 0\n"
            # Ties broken by route_code, same as the slow path just below and
            # as _concentration()'s fast path.
            "ORDER BY avg_min DESC NULLS LAST, route_code",
            agency_id,
            *params,
        )
    else:
        # Grouping by route_code -> every group has >= 1 sample, no
        # empty-input case; the average is never NULL for a non-empty group.
        # Ties broken by route_code, as in _concentration().
        by_route = _grain_sum_by(_grain_window(_require_grain(grain), cur_ctx), key_index=1)
        rows = [
            {"route_code": code, "avg_min": (total / n) / 60.0}
            for code, (n, total) in sorted(by_route.items(), key=lambda kv: (-(kv[1][1] / kv[1][0]), kv[0]))
        ]

    if not rows:
        return {"routes": [], "delayed_count": 0}

    delayed_count = sum(1 for r in rows if r["avg_min"] is not None and r["avg_min"] >= 2.0)
    top_n = rows[:limit]
    codes = [r["route_code"] for r in top_n]
    names = await _route_short_names(agency_id, codes, conn)
    return {
        "routes": [
            {
                "route_code": r["route_code"],
                "route_short_name": names.get(r["route_code"]),
                "avg_min": round(float(r["avg_min"]), 2),
            }
            for r in top_n
        ],
        "delayed_count": delayed_count,
    }


async def _peak_hour(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> dict | None:
    """24-bucket avg by EXTRACT(HOUR FROM scheduled_time) + peak hour.

    ``ch`` and ``grain`` are accepted (and unused) only so callers can pass
    them uniformly alongside the other stage helpers (e.g.
    ``compute_overview_summary``'s pool-gather path's ``_own_conn``) — this
    function has no live-fallback branch: ``agg_route_hour`` always applies
    ``time_band`` itself, so there is nothing here for the ClickHouse dedup
    path (or the shared grain) to serve.

    Reads from ``agg_route_hour``, which is a fixed analyze-period rollup
    (no date column). Consequence: the date range and DOW in ``ctx`` are
    ignored — but ``service``, ``routes``, AND ``time_band`` all apply,
    because ``agg_route_hour`` does carry a TIME column.
    """
    n = 2
    params: list = []
    parts: list[str] = []
    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1
    if ctx.routes:
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1
    tb_frag, tb_params, n = _time_band_sql_on("scheduled_time", ctx.time_band, n)
    if tb_frag:
        parts.append(tb_frag)
        params.extend(tb_params)
    where_clause = (" AND " + " AND ".join(parts)) if parts else ""
    sql = (
        "SELECT EXTRACT(HOUR FROM scheduled_time)::int AS h,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
        "FROM agg_route_hour\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY EXTRACT(HOUR FROM scheduled_time)"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    return _peak_from_hour_rows(rows)


async def _peak_hour_by_dow(
    agency_id: int, ctx: RangeCtx, conn, dow_group: str, ch=None, grain: _Grain | None = None
) -> dict | None:
    """24-hour avg delay restricted to weekday (``'weekday'``) or weekend
    (``'weekend'``) only.

    Fast path reads the per-day/hour ``agg_hour_daily`` (filtering dates by
    DOW), a sample-weighted average across the range — sub-second instead of
    the raw dedup scan that used to dominate Overview's cold load. That table is
    aggregated across all routes/services, so a ``service``/``routes`` filter,
    or any ``time_band`` other than ``'all'``, has to leave it.

    ``time_band != 'all'`` (the case that also puts every other stage helper on
    its slow path) is served from the shared grain. The remaining case —
    ``time_band == 'all'`` but a ``service``/``routes`` filter, where nothing
    else in the payload needs live ``updates`` and so no grain was fetched —
    still runs its own dedup scan. That scan is the only ClickHouse work in an
    otherwise all-Postgres request, so it degrades to ``None`` on failure
    instead of failing the whole payload (see the ``except`` below).

    Either way, ``dow_group`` REPLACES ``ctx.dow``: the question asked here is
    "weekday vs weekend", regardless of any day-of-week the user had already
    narrowed to. That is exactly why the grain carries no DOW filter of its own
    (see :func:`_fetch_grain`).
    """
    if ctx.time_band == "all" and ctx.service == "all" and not ctx.routes:
        dow_pred = "BETWEEN 1 AND 5" if dow_group == "weekday" else "IN (6, 7)"
        sql = (
            "SELECT hour AS h,\n"
            "       SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min\n"
            "FROM agg_hour_daily\n"
            "WHERE agency_id = $1 AND date >= ($2::text)::date AND date <= ($3::text)::date\n"
            f"  AND EXTRACT(ISODOW FROM date) {dow_pred}\n"
            "GROUP BY hour"
        )
        rows = await conn.fetch(sql, agency_id, str(ctx.from_date), str(ctx.to_date))
        return _peak_from_hour_rows(rows)

    if grain is not None:
        # Iterates `grain.rows` directly rather than going through
        # `_grain_window` (the DOW filter here is `dow_group`, not `ctx.dow`),
        # so the coverage invariant has to be asserted explicitly.
        _check_grain_covers(grain, ctx.from_date, ctx.to_date)
        # `hour is None` <=> the row's scheduled_time was NULL, which is what
        # the live query below excludes with `WHERE scheduled_time IS NOT NULL`.
        by_hour: dict[int, list[int]] = {}
        lo, hi = ctx.from_date, ctx.to_date
        for d, _route_code, _service_type, hour, samples, sum_delay, _sum_late in grain.rows:
            if hour is None or not (lo <= d <= hi) or not _dow_matches(d, dow_group):
                continue
            slot = by_hour.setdefault(hour, [0, 0])
            slot[0] += samples
            slot[1] += sum_delay
        return _peak_from_hour_rows(
            [{"h": h, "avg_min": (total / n) / 60.0} for h, (n, total) in sorted(by_hour.items())]
        )

    if ch is None:
        raise RuntimeError("_peak_hour_by_dow's live fallback requires a ClickHouse client")

    overridden = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow=dow_group,  # type: ignore[arg-type]
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )
    cte_sql, ch_params = _dedup_cte_ch(overridden)
    # scheduled_time is a zero-padded 'HH:MM:SS' String in ClickHouse (not a
    # native TIME column) — see api.range.time_band_clause_ch's docstring —
    # so the hour is read off the first two characters rather than EXTRACT().
    # OrNull, not a bare cast: ingest now zero-pads every scheduled_time's
    # hour (pipeline/strategies/static_join.py), but this degrades any
    # already-ingested pre-fix row out of the histogram instead of raising
    # CANNOT_PARSE_TEXT — matching the same OrNull already used a few lines
    # up in this file's time_band != 'all' branch and in rankings.py.
    hour_expr = "toUInt8OrNull(substring(scheduled_time, 1, 2))"
    try:
        result = await ch.query(
            f"WITH {cte_sql}\n"
            f"SELECT {hour_expr} AS h,\n"
            "       avg(dep_delay) / 60.0 AS avg_min\n"
            "FROM deduped\n"
            "WHERE scheduled_time IS NOT NULL\n"
            f"GROUP BY {hour_expr}\n"
            "HAVING h IS NOT NULL",
            parameters={"agency_id": agency_id, **ch_params},
        )
    except Exception:
        # This is the ONLY ClickHouse call the whole Overview payload makes in
        # this request shape (``time_band == 'all'`` + a service/routes filter):
        # headline, movers, concentration, top_delayed, service_split and the
        # sparkline are all served from Postgres ``agg_*`` tables here. So a
        # ClickHouse hiccup must degrade THIS field rather than 500 the whole
        # response — ``peak_hour_weekday``/``peak_hour_weekend`` are already
        # ``PeakHour | None = None`` in api.routers.overview, and ``None`` is
        # exactly what ``_peak_from_hour_rows`` returns for "no data". Same
        # try/except-and-degrade shape as
        # pipeline.reports.network.compute_network_summary's per-agency probe.
        _log.warning(
            "ClickHouse peak-hour probe failed for agency %s (%s) — degrading peak_hour_%s to null",
            agency_id,
            dow_group,
            dow_group,
            exc_info=True,
        )
        return None
    rows = _ch_rows(result)
    return _peak_from_hour_rows(rows)


def _peak_from_hour_rows(rows) -> dict | None:
    """Shape ``(h, avg_min)`` rows into the ``by_hour[24]`` + peak payload."""
    if not rows:
        return None
    by_hour: list[float | None] = [None] * 24
    for r in rows:
        if r["avg_min"] is None:
            continue
        h = int(r["h"])
        if 0 <= h < 24:
            by_hour[h] = round(float(r["avg_min"]), 2)
    valid = [h for h in range(24) if by_hour[h] is not None]
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


async def _service_split_daily(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> list[dict]:
    """Per-day breakdown of 平日 vs 土日祝 avg delay over ``ctx``.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted per-(date, service_type) average. Slow path reads the
    shared grain (:func:`_fetch_grain`) so the hour-of-day filter is honored.

    Returns a list of ``{date: ISO str, weekday: float|None, weekend:
    float|None}`` rows sorted by date. Dates with neither service_type
    are silently dropped.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = (
            "SELECT date, service_type,\n"
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY date, service_type\n"
            "ORDER BY date"
        )
        rows = await conn.fetch(sql, agency_id, *params)
    else:
        # Grouping by (date, service_type) -> every group has >= 1 sample.
        acc: dict[tuple[date, str | None], list[int]] = {}
        for row in _grain_window(_require_grain(grain), ctx):
            slot = acc.setdefault((row[0], row[2]), [0, 0])
            slot[0] += row[4]
            slot[1] += row[5]
        rows = [
            {"date": d, "service_type": service_type, "avg": (total / n) / 60.0}
            for (d, service_type), (n, total) in sorted(acc.items(), key=lambda kv: kv[0][0])
        ]
    by_date: dict[str, dict[str, float | None]] = {}
    for r in rows:
        d_raw = r["date"]
        d = d_raw if isinstance(d_raw, str) else d_raw.isoformat()
        st = r["service_type"]
        avg = float(r["avg"]) if r["avg"] is not None else None
        by_date.setdefault(d, {})[st] = avg
    out: list[dict] = []
    for d in sorted(by_date):
        bucket = by_date[d]
        out.append(
            {
                "date": d,
                "weekday": bucket.get("平日"),
                "weekend": bucket.get("土日祝"),
            }
        )
    return out


async def _movers(
    agency_id: int, cur_ctx: RangeCtx, base_ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None
) -> dict:
    """Top-10 worsened + top-10 improved routes by signed delta_min.

    Compares ``cur_ctx`` against ``base_ctx`` (both built upstream by
    ``compute_overview_summary`` so the comparison is a true 7-day
    week-over-week regardless of the user's selected range). Requires
    >= 10 samples in BOTH windows for a route to enter the ranking — a
    route with a handful of obs can swing a huge delta_pct and would
    otherwise dominate top-3 with low statistical confidence.

    Frontend card variant slices :code:`.slice(0, 3)`; modal variant
    uses the full 10.
    """
    cur = await _per_route_avg(agency_id, cur_ctx, conn, ch=ch, grain=grain)
    prv = await _per_route_avg(agency_id, base_ctx, conn, ch=ch, grain=grain)
    common = set(cur) & set(prv)
    # (route_code, delta_min_2dp, delta_pct_1dp, delta_min_raw). The RAW delta
    # is carried purely to rank on: ranking on the rounded value manufactures
    # ties that don't exist — real dev data, agency 1 over a wide morning
    # window, has two routes at -1.7244 and -1.7203, which both round to -1.72.
    # Ranking on the rounded value made the top-10 cutoff between them a
    # coin-flip; ranking on the raw value orders them by their actual deltas.
    deltas: list[tuple[str, float, float | None, float]] = []
    MIN_SAMPLES = 10
    # A previous-window average below this floor makes delta_pct meaningless:
    # dividing by a near-zero baseline can turn a trivial absolute change into
    # a triple-digit-or-larger swing that reads as a real signal but isn't,
    # even past the MIN_SAMPLES gate above (a small sample can still average
    # to a near-zero delay). The floor matches this app's own delay-severity
    # ramp elsewhere, which already treats anything below it as background
    # noise rather than a noticeable delay.
    MIN_PRV_AVG_FOR_PCT_MIN = 1.5
    for code in common:
        cur_avg, cur_n = cur[code]
        prv_avg, prv_n = prv[code]
        if prv_avg == 0:
            continue
        if cur_n < MIN_SAMPLES or prv_n < MIN_SAMPLES:
            continue
        d_min = cur_avg - prv_avg
        d_pct = round((d_min / prv_avg) * 100.0, 1) if abs(prv_avg) >= MIN_PRV_AVG_FOR_PCT_MIN else None
        deltas.append((code, round(d_min, 2), d_pct, d_min))
    # `(raw delta, route_code)` is a TOTAL order — route_codes are dict keys, so
    # they're distinct — which is what makes the ranking reproducible: the order
    # `common` happens to be iterated in cannot influence the result. It used to:
    # ranking on the rounded delta left genuine ties, and Python's stable sort
    # then preserved the *set* iteration order, leaking per-process string-hash
    # randomization into which routes made the top-10. The same request against
    # the same data returned different movers after an app restart.
    deltas.sort(key=lambda x: (x[3], x[0]))
    # Partition by sign so "worse" only contains routes with positive
    # delta_min and "better" only routes with negative delta_min. With
    # the wider top-10 limit, sign-partitioning is the right way to
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
    history = await _route_weekly_history(agency_id, codes, cur_ctx, conn, weeks_back=4, ch=ch, grain=grain)

    def _entry(code, dm, dp, direction):
        """Serialize one mover row (deltas, absolute averages, streak, sparkline)."""
        pts = [v for v in history.get(code, []) if v is not None]
        return {
            "route_code": code,
            "route_short_name": names.get(code),
            "delta_min": dm,
            "delta_pct": dp,
            # Absolute averages for both windows so the UI can show
            # "last week X min → this week Y min" instead of a bare Δ%.
            "current_avg_min": round(cur[code][0], 1),
            "previous_avg_min": round(prv[code][0], 1),
            "streak_weeks": _streak_weeks(history.get(code, []), direction=direction),
            "sparkline_points": pts,
        }

    return {
        "worse": [_entry(c, dm, dp, "up") for c, dm, dp, _raw in worse],
        "better": [_entry(c, dm, dp, "down") for c, dm, dp, _raw in better],
    }


async def _service_split(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> dict[str, float]:
    """avg_min per service_type (typically '平日' / '土日祝').

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average. Slow path reads the shared grain
    (:func:`_fetch_grain`) so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT service_type,\n"
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY service_type",
            agency_id,
            *params,
        )
    else:
        # Sorted by service_type purely for a stable key order in the JSON
        # envelope; the ClickHouse `GROUP BY service_type` this replaces
        # emitted groups in an arbitrary order.
        by_service = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=2)
        rows = [
            {"service_type": service_type, "avg_min": (total / n) / 60.0}
            for service_type, (n, total) in sorted(((k, v) for k, v in by_service.items() if k), key=lambda kv: kv[0])
        ]
    return {
        r["service_type"]: round(float(r["avg_min"]), 2) for r in rows if r["service_type"] and r["avg_min"] is not None
    }


async def _daily_sparkline(agency_id: int, ctx: RangeCtx, conn, ch=None, grain: _Grain | None = None) -> list[float]:
    """Daily avg_min points (oldest first) over ``ctx``.

    Returns the FULL daily series. The frontend hero card slices the
    trailing 7 days for the inline sparkline; the modal variant uses the
    full series (typically 30+ points for a 30-day default range).

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average per date. Slow path reads the shared grain
    (:func:`_fetch_grain`) so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT date AS day,\n"
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY date\n"
            "ORDER BY date ASC",
            agency_id,
            *params,
        )
    else:
        by_day = _grain_sum_by(_grain_window(_require_grain(grain), ctx), key_index=0)
        rows = [
            {"day": d, "avg_min": (total / n) / 60.0} for d, (n, total) in sorted(by_day.items(), key=lambda kv: kv[0])
        ]
    pts = [round(float(r["avg_min"]), 2) for r in rows if r["avg_min"] is not None]
    return pts


@perf.timed("reports.overview")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    locale: str = "ja",
    *,
    pool=None,
    ch=None,
) -> dict:
    """Build the 概況 payload for one agency over ``ctx``.

    Headline math uses the LAST 7 days of ``ctx`` and compares against
    the 7-day window immediately prior, so the "this week vs last week"
    copy is honest regardless of how the user has widened the ctx range.
    Concentration / peak / service_split / sparkline still aggregate over
    the full ctx to surface broader patterns.

    When ``pool`` is supplied (non-None), the ten stage queries are
    dispatched as concurrent asyncio tasks, each acquiring its own
    connection from the pool so they can truly run in parallel.  The two
    ``_peak_hour_by_dow`` calls — identified as 96 % of cold-load time in
    the baseline measurement — are the primary beneficiaries.  When
    ``pool`` is None (the default) the existing sequential path with
    per-stage timed_blocks is used unchanged, preserving behaviour for
    tests and ad-hoc callers.

    ``ch`` is the ClickHouse client used by the slow path (``updates`` itself
    now lives in ClickHouse, not Postgres). Defaults to ``None`` for callers
    that only ever exercise the ``ctx.time_band == 'all'`` fast path (most
    existing tests); real dispatch (the ``/overview/summary`` route) always
    passes the real client.

    On the slow path (``ctx.time_band != 'all'``) the shared ClickHouse query
    the payload needs is issued up front (:func:`_fetch_grain`) and handed to
    every stage helper, which then derives its own numbers from it in Python.
    Previously each helper ran its own dedup scan — ~12 per request. It is the
    only query for any ``ctx`` wide enough for the grain to also cover
    :func:`_route_weekly_history`'s 4-week sparkline span (every default 30-day
    request); a narrower ``ctx`` costs that one helper a second scan. The fast
    path is unchanged: ``grain`` stays ``None`` and every helper reads its
    ``agg_*`` table exactly as before.
    """
    grain: _Grain | None = None
    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("overview slow path requires a ClickHouse client")
        async with perf.timed_block("overview.grain"):
            grain = await _fetch_grain(agency_id, ctx, ch)

    async with perf.timed_block("overview.latest_date"):
        latest = await _latest_data_date(agency_id, ctx, conn, ch=ch, grain=grain)
    # If no data anywhere in ctx, anchor to ctx.to_date so empty payload
    # still has a sensible window_to.
    anchor = latest if latest is not None else ctx.to_date

    # Build current + baseline 7-day windows anchored at `anchor`, but
    # clamped inside ctx.
    cur_to = anchor
    cur_from = max(cur_to - timedelta(days=6), ctx.from_date)
    cur_ctx = RangeCtx(
        from_date=cur_from,
        to_date=cur_to,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )
    base_to = cur_from - timedelta(days=1)
    base_from = base_to - timedelta(days=6)
    base_ctx = RangeCtx(
        from_date=base_from,
        to_date=base_to,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )

    if pool is None:
        async with perf.timed_block("overview.headline"):
            avg_min, samples = await _headline_stats(agency_id, cur_ctx, conn, ch=ch, grain=grain)
            baseline_avg, _ = await _headline_stats(agency_id, base_ctx, conn, ch=ch, grain=grain)

        delta_min = None
        delta_pct = None
        if avg_min is not None and baseline_avg is not None:
            delta_min = round(avg_min - baseline_avg, 2)
            if baseline_avg != 0:
                delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

        async with perf.timed_block("overview.movers"):
            movers = await _movers(agency_id, cur_ctx, base_ctx, conn, ch=ch, grain=grain)
        async with perf.timed_block("overview.concentration"):
            concentration = await _concentration(agency_id, ctx, conn, ch=ch, grain=grain)
        async with perf.timed_block("overview.top_delayed"):
            top_delayed = await _top_delayed_routes(agency_id, cur_ctx, conn, ch=ch, grain=grain)
        async with perf.timed_block("overview.peaks"):
            peak = await _peak_hour(agency_id, ctx, conn)
            peak_weekday = await _peak_hour_by_dow(agency_id, ctx, conn, "weekday", ch=ch, grain=grain)
            peak_weekend = await _peak_hour_by_dow(agency_id, ctx, conn, "weekend", ch=ch, grain=grain)
        async with perf.timed_block("overview.service_split"):
            service_split = await _service_split(agency_id, ctx, conn, ch=ch, grain=grain)
            service_split_daily = await _service_split_daily(agency_id, ctx, conn, ch=ch, grain=grain)
        async with perf.timed_block("overview.sparkline"):
            # Hero card slices `.slice(-7)`; modal shows full series.
            sparkline_points = await _daily_sparkline(agency_id, ctx, conn, ch=ch, grain=grain)

    else:
        # Pool-gather path — each task acquires its own pooled connection
        # so all ten queries can run concurrently. A single asyncpg
        # connection cannot multiplex queries; pool.acquire() queues when
        # saturated, so concurrency is naturally bounded by pool size. `ch`
        # (a single shared ClickHouse client, not pool-backed) is closed
        # over directly rather than threaded through `_own_conn`'s *args.
        # No per-stage timed_blocks here; the top-level reports.overview
        # label captures the wall-clock total. On the slow path the shared
        # ClickHouse grain query has already run (above, before this branch),
        # so what still fans out here is mostly Postgres work — `_peak_hour`,
        # `_route_short_names`, and every fast-path helper's `agg_*` read (plus
        # `_route_weekly_history`'s live scan, in the narrow-`ctx` case where
        # the grain can't cover its span).
        async def _own_conn(fn, *args):
            """Acquire a pool connection, call ``fn(*args, conn, ch=ch, grain=grain)``, release."""
            async with pool.acquire() as c:
                return await fn(*args, c, ch=ch, grain=grain)

        async def _peak_dow(group: str) -> dict | None:
            """Acquire a pool connection and run ``_peak_hour_by_dow`` for ``group``."""
            async with pool.acquire() as c:
                return await _peak_hour_by_dow(agency_id, ctx, c, group, ch=ch, grain=grain)

        (
            (avg_min, samples),
            (baseline_avg, _),
            movers,
            concentration,
            top_delayed,
            peak,
            peak_weekday,
            peak_weekend,
            service_split,
            service_split_daily,
            sparkline_points,
        ) = await asyncio.gather(
            _own_conn(_headline_stats, agency_id, cur_ctx),
            _own_conn(_headline_stats, agency_id, base_ctx),
            _own_conn(_movers, agency_id, cur_ctx, base_ctx),
            _own_conn(_concentration, agency_id, ctx),
            _own_conn(_top_delayed_routes, agency_id, cur_ctx),
            _own_conn(_peak_hour, agency_id, ctx),
            _peak_dow("weekday"),
            _peak_dow("weekend"),
            _own_conn(_service_split, agency_id, ctx),
            _own_conn(_service_split_daily, agency_id, ctx),
            _own_conn(_daily_sparkline, agency_id, ctx),
        )

        delta_min = None
        delta_pct = None
        if avg_min is not None and baseline_avg is not None:
            delta_min = round(avg_min - baseline_avg, 2)
            if baseline_avg != 0:
                delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

    return {
        "headline": {
            "avg_min": avg_min,
            "baseline_avg_min": baseline_avg,
            "delta_min": delta_min,
            "delta_pct": delta_pct,
            "samples": samples,
            "window_from": cur_ctx.from_date.isoformat(),
            "window_to": cur_ctx.to_date.isoformat(),
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
    }
