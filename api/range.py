"""Range / DOW / time-band context shared across analytical endpoints.

A request to a v2 endpoint passes ``?from=YYYY-MM-DD&to=YYYY-MM-DD&dow=...&time_band=...``
which FastAPI resolves to a :class:`RangeCtx` via the :func:`get_range_ctx`
dependency. SQL helpers in this module turn the context into ``WHERE`` clause
fragments + parameter lists ready to splice into asyncpg queries.

Defaults: last 30 days inclusive, all DOW, all time bands. Server clamps
ranges wider than 365 days to avoid runaway scans.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from fastapi import Query

DowFilter = Literal["all", "weekday", "weekend"]
TimeBand = Literal[
    "all",
    "morning",
    "forenoon",
    "noon",
    "afternoon",
    "evening",
    "night",
    "late_night",
]
ServiceType = Literal["all", "平日", "土日祝"]

# (start_inclusive, end_exclusive) clock times as 'HH:MM' strings. Migration
# 0011 made `scheduled_time` a TIME column, so `time_band_clause` casts both
# sides of the comparison to TIME — these literals are sent over the wire as
# text and cast server-side. '24:00' is a valid Postgres TIME (end-of-day).
_TIME_BAND_RANGES: dict[str, tuple[str, str]] = {
    "morning": ("05:00", "09:00"),
    "forenoon": ("09:00", "12:00"),
    "noon": ("12:00", "14:00"),
    "afternoon": ("14:00", "17:00"),
    "evening": ("17:00", "20:00"),
    "night": ("20:00", "24:00"),
    "late_night": ("00:00", "05:00"),
}

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 365


@dataclass(frozen=True)
class RangeCtx:
    """Resolved request context — defaults applied, range clamped."""

    from_date: date
    to_date: date
    dow: DowFilter = "all"
    time_band: TimeBand = "all"
    service: ServiceType = "all"
    routes: tuple[str, ...] = ()

    @property
    def days(self) -> int:
        return (self.to_date - self.from_date).days + 1


def get_range_ctx(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    dow: DowFilter = Query(default="all"),
    time_band: TimeBand = Query(default="all"),
    service: ServiceType = Query(default="all"),
    routes: str | None = Query(default=None, description="Comma-separated route_codes"),
) -> RangeCtx:
    """FastAPI dependency: parse query params into a :class:`RangeCtx`.

    Missing dates fall back to ``today - 30d`` / ``today``. Ranges wider than
    :data:`MAX_RANGE_DAYS` are clamped at the start (newer end stays as given)
    so the most recent data is preserved.
    """
    today = date.today()
    to_date = parse_iso_date(to) or today
    from_date = parse_iso_date(from_) or (to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1))

    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        from_date = to_date - timedelta(days=MAX_RANGE_DAYS - 1)

    route_tuple: tuple[str, ...] = ()
    if routes:
        # Cap at 100 codes to bound query and JSON envelope; UI surface is much
        # smaller than that. Drop empties, dedupe while preserving order.
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in routes.split(","):
            r = raw.strip()
            if r and r not in seen:
                seen.add(r)
                cleaned.append(r)
            if len(cleaned) >= 100:
                break
        route_tuple = tuple(cleaned)

    return RangeCtx(
        from_date=from_date,
        to_date=to_date,
        dow=dow,
        time_band=time_band,
        service=service,
        routes=route_tuple,
    )


def parse_iso_date(s: str | None) -> date | None:
    """Lenient ISO-8601 date parser: ``None``/empty/invalid → ``None``."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# SQL clause builders
#
# Each builder returns ``(fragment, params)`` where ``fragment`` is a snippet
# meant to be ANDed into a larger WHERE, and ``params`` is the list of
# positional values the asyncpg call should append. The ``next_param`` arg
# lets the caller thread positional placeholder numbering across multiple
# clause builders.
# ---------------------------------------------------------------------------


def date_range_clause(
    column: str,
    ctx: RangeCtx,
    next_param: int,
    *,
    column_type: str = "timestamptz",
) -> tuple[str, list, int]:
    """Date-range WHERE fragment for ``column``.

    ``column_type="timestamptz"`` (``updates.captured_at``): emits half-open
    timestamptz bounds (midnight-to-midnight in the session timezone — JST,
    set by api/main._init_connection) instead of the previous
    ``column::date BETWEEN $a AND $b``. The cast on the *column* side defeated
    ``idx_updates_agency_at`` and forced full seq scans of ``updates``; the
    half-open form is index-sargable and date-equivalent under the same
    session TZ (verified row-count-identical on live data; a 7-day window
    scan went 340ms → 70ms).

    ``column_type="text_date"`` (agg tables store ISO date strings): keeps the
    ``column::date BETWEEN`` form — those tables are small aggregates with no
    index at stake.

    The ``::text`` coercion keeps asyncpg sending the params as TEXT,
    mirroring time_band_clause; ``str()`` normalizes the mixed caller types
    (ISO str from the API ctx, datetime.date from tests).
    """
    if column_type == "text_date":
        fragment = f"{column}::date BETWEEN (${next_param}::text)::date AND (${next_param + 1}::text)::date"
    else:
        fragment = (
            f"({column} >= ((${next_param}::text)::date)::timestamptz "
            f"AND {column} < (((${next_param + 1}::text)::date + 1))::timestamptz)"
        )
    return fragment, [str(ctx.from_date), str(ctx.to_date)], next_param + 2


def dow_clause(
    column: str,
    ctx: RangeCtx,
    next_param: int,
) -> tuple[str, list, int]:
    """``column`` is a date/timestamp column from which to derive day-of-week."""
    if ctx.dow == "all":
        return "TRUE", [], next_param
    if ctx.dow == "weekday":
        return f"EXTRACT(ISODOW FROM {column}::date) BETWEEN 1 AND 5", [], next_param
    # weekend: Saturday (6) + Sunday (7)
    return f"EXTRACT(ISODOW FROM {column}::date) IN (6, 7)", [], next_param


def time_band_clause(
    column: str,
    ctx: RangeCtx,
    next_param: int,
) -> tuple[str, list, int]:
    """Return a WHERE fragment filtering ``column`` (a TIME column) to the
    range named by ``ctx.time_band``.

    Each placeholder is cast as ``(${n}::text)::time`` so asyncpg sends
    the Python ``str`` over the wire as TEXT — without the ``::text``
    coercion, asyncpg's prepared-statement type inference would try to
    encode the string as ``datetime.time`` and fail. The server then
    parses the text into TIME for the comparison.
    """
    if ctx.time_band == "all":
        return "TRUE", [], next_param
    start, end = _TIME_BAND_RANGES[ctx.time_band]
    fragment = f"({column}::time >= (${next_param}::text)::time AND {column}::time < (${next_param + 1}::text)::time)"
    return fragment, [start, end], next_param + 2


def build_updates_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """Combined WHERE fragment for the ``updates`` table.

    Applies date range + DOW + time-band + service + routes filters. Returns
    a single AND-joined fragment plus the asyncpg-style parameter list.
    """
    parts: list[str] = []
    params: list = []
    n = next_param

    frag, p, n = date_range_clause("captured_at", ctx, n)
    parts.append(frag)
    params.extend(p)

    frag, p, n = dow_clause("captured_at", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    frag, p, n = time_band_clause("scheduled_time", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1

    if ctx.routes:
        # ANY array — efficient with the existing index on route_code
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1

    return " AND ".join(parts), params, n


def time_band_case_sql(column: str) -> str:
    """SQL CASE mapping a TIME column to its `_TIME_BAND_RANGES` band key.

    Generated from `_TIME_BAND_RANGES` so analyze's bucketing can never drift
    from `time_band_clause`'s filter. NULL / out-of-range -> 'none'.
    """
    arms = "\n".join(
        f"            WHEN {column} >= '{start}'::time AND {column} < '{end}'::time THEN '{band}'"
        for band, (start, end) in _TIME_BAND_RANGES.items()
    )
    return (
        "CASE\n"
        f"            WHEN {column} IS NULL THEN 'none'\n"
        f"{arms}\n"
        "            ELSE 'none'\n"
        "        END"
    )


def build_agg_stop_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for `agg_stop_daily` (date / DOW / service / time_band).

    `time_band` matches the stored band KEY directly (analyze pre-bucketed it
    via `time_band_case_sql`), not a scheduled_time range.
    """
    parts: list[str] = []
    params: list = []
    n = next_param

    frag, p, n = date_range_clause("date", ctx, n, column_type="text_date")
    parts.append(frag)
    params.extend(p)

    frag, p, n = dow_clause("date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1

    if ctx.time_band != "all":
        parts.append(f"time_band = ${n}")
        params.append(ctx.time_band)
        n += 1

    return " AND ".join(parts), params, n


def build_agg_daily_trend_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for ``agg_daily_trend`` (aggregated; no time-band column)."""
    parts: list[str] = []
    params: list = []
    n = next_param

    frag, p, n = date_range_clause("date", ctx, n, column_type="text_date")
    parts.append(frag)
    params.extend(p)

    frag, p, n = dow_clause("date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    return " AND ".join(parts), params, n
