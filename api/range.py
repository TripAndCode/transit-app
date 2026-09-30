"""Range / DOW / time-band context shared across analytical endpoints.

A request to a v2 endpoint passes ``?from=YYYY-MM-DD&to=YYYY-MM-DD&dow=...&time_band=...``
which FastAPI resolves to a :class:`RangeCtx` via the :func:`get_range_ctx`
dependency. SQL helpers in this module turn the context into ``WHERE`` clause
fragments + parameter lists ready to splice into asyncpg queries.

Defaults: last 30 days inclusive, all DOW, all time bands. Every entry
point — query params, request body, a conversation's stored filters — goes
through :func:`clamp_range_ctx`, which clamps both boundaries to today and
the window to 365 days to avoid runaway scans.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, cast, get_args
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query

_JST = ZoneInfo("Asia/Tokyo")


def jst_today() -> date:
    """Today's date in Asia/Tokyo.

    Every agg_*/analyze query is bucketed against the JST civil calendar
    (api/main.py pins the DB session to the same zone). Using the
    server's local date here caused a real ~20%-of-rows mis-bucketing bug
    in analyze() before (UTC-vs-JST) - this avoids the same class of bug
    for the request-side default date-range window.
    """
    return datetime.now(_JST).date()


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

WEEKDAY_NAMES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ISO weekday sets (1=Monday..7=Sunday) of the legacy dow groups. A weekday
# list that equals one of these is stored as the group's name, so every
# reader that branches on "weekday"/"weekend" keeps working unchanged.
_DOW_GROUPS: dict[str, frozenset[int]] = {
    "weekday": frozenset(range(1, 6)),
    "weekend": frozenset({6, 7}),
    "all": frozenset(range(1, 8)),
}

_DOW_ERROR = "invalid dow: expected all, weekday, weekend, or a comma list of mon..sun"


def canonical_dow(value: object) -> str:
    """Validate a ``dow`` value into its one canonical spelling.

    A comma list of weekday names is de-duplicated, put in Monday-first
    order, and folded into ``weekday``/``weekend``/``all`` when it names
    exactly that group. Anything else is a 422, like the other enums.
    """
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=_DOW_ERROR)
    if value in ("all", "weekday", "weekend"):
        return value
    names = value.split(",")
    if any(name not in WEEKDAY_NAMES for name in names):
        raise HTTPException(status_code=422, detail=_DOW_ERROR)
    days = frozenset(WEEKDAY_NAMES.index(name) + 1 for name in names)
    for group, members in _DOW_GROUPS.items():
        if days == members:
            return group
    return ",".join(WEEKDAY_NAMES[day - 1] for day in sorted(days))


def dow_isodays(dow: str) -> frozenset[int] | None:
    """The ISO weekdays a canonical ``dow`` selects; ``None`` means every day."""
    if dow == "all":
        return None
    if dow in _DOW_GROUPS:
        return _DOW_GROUPS[dow]
    return frozenset(WEEKDAY_NAMES.index(name) + 1 for name in dow.split(","))


# (start_inclusive, end_exclusive) clock times as 'HH:MM' strings. Migration
# 0011 made `scheduled_time` a TIME column, so `time_band_case_sql` casts both
# sides of the comparison to TIME — these literals are sent over the wire as
# text and cast server-side. '24:00' is a valid Postgres TIME (end-of-day).
#
# Public because it is the single definition of the band grid: the Postgres
# CASE (`time_band_case_sql`), the ClickHouse filter (`time_band_clause_ch`)
# and pipeline/reports/filters.py's TIME-column filter all read it, and a
# second copy would let analyze's bucketing drift from the query filters.
TIME_BAND_RANGES: dict[str, tuple[str, str]] = {
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
# Bounds both the SQL predicate and the JSON envelope; the UI's own route
# picker surfaces far fewer than this.
MAX_ROUTE_FILTERS = 100
# GTFS stop_id is free-form text; this bounds the predicate and the echo.
MAX_STOP_ID_LEN = 128
_HOUR_ERROR = "invalid hour: expected 0-23 or an inclusive range a-b"


@dataclass(frozen=True)
class RangeCtx:
    """Resolved request context — defaults applied, range clamped."""

    from_date: date
    to_date: date
    # Canonical per canonical_dow: a legacy group or a Monday-first weekday list.
    dow: str = "all"
    time_band: TimeBand = "all"
    service: ServiceType = "all"
    routes: tuple[str, ...] = ()
    # Inclusive hour range; mutually exclusive with a time_band other than "all".
    hour: tuple[int, int] | None = None
    stop: str | None = None
    # GTFS direction_id; the URL name is `dir`.
    direction: int | None = None

    @property
    def days(self) -> int:
        return (self.to_date - self.from_date).days + 1


def _coerce_enum(value: str, allowed: tuple[str, ...], field: str) -> str:
    """Return ``value`` if it names one of ``allowed``, else raise 422.

    Silently folding an unrecognised value to ``"all"`` answers a different
    question than the caller asked and hides client/stored-state bugs.
    """
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {field}: expected one of {', '.join(allowed)}",
        )
    return value


def _coerce_hour(value: object) -> tuple[int, int] | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=_HOUR_ERROR)
    parts = value.split("-")
    if len(parts) not in (1, 2) or not all(p.isdigit() and len(p) <= 2 for p in parts):
        raise HTTPException(status_code=422, detail=_HOUR_ERROR)
    start, end = int(parts[0]), int(parts[-1])
    if start > 23 or end > 23 or start > end:
        raise HTTPException(status_code=422, detail=_HOUR_ERROR)
    return start, end


def _coerce_stop(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > MAX_STOP_ID_LEN:
        raise HTTPException(
            status_code=422, detail=f"invalid stop: expected a stop_id of at most {MAX_STOP_ID_LEN} characters"
        )
    return value.strip() or None


def _coerce_direction(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or value not in ("0", "1", 0, 1):
        raise HTTPException(status_code=422, detail="invalid dir: expected 0 or 1")
    return int(value)


def _coerce_date(value: str | date | None, field: str) -> date | None:
    """Parse one boundary of the requested window.

    ``None`` and the empty string mean "not supplied" and yield ``None`` so
    the caller can apply its default. Anything else non-empty must be a real
    ISO-8601 date: a malformed value is a client error (422), never a silent
    fall-through to the default window, which would return a confident answer
    for a period nobody asked about.

    A value of the wrong type is a 422 for the same reason. A stored
    ``filter_ctx`` is arbitrary client JSON, so this boundary can arrive as a
    number or a list; those must not reach ``.strip()`` and surface as a 500.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"invalid {field}: expected YYYY-MM-DD")
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid {field}: expected YYYY-MM-DD") from None


def clamp_range_ctx(
    *,
    from_: str | date | None,
    to: str | date | None,
    dow: str = "all",
    time_band: str = "all",
    service: str = "all",
    routes: Iterable[str] = (),
    hour: object = None,
    stop: object = None,
    direction: object = None,
) -> RangeCtx:
    """Validate and clamp raw filter values into a :class:`RangeCtx`.

    The single entry point for every source of a RangeCtx — query params,
    a request body, and a conversation's persisted ``filter_ctx`` — because
    a persisted filter is still client input: it was accepted from a client,
    stored verbatim, and can be replayed long after the code that wrote it
    changed. Hand-copied variants of this logic drifted apart before, each
    enforcing a different subset of the rules below.

    Rules, in order:

    * absent dates default to a trailing :data:`DEFAULT_RANGE_DAYS` window;
      a malformed non-empty date is a 422;
    * neither boundary may exceed :func:`jst_today` — no aggregate holds a
      future date, so a future bound can only widen the scan. Both ends are
      clamped *before* the reversed-range swap, so the swap can't reopen a
      future ``to_date``;
    * a reversed range is swapped rather than rejected;
    * a window wider than :data:`MAX_RANGE_DAYS` is clamped at the *start*,
      preserving the most recent data;
    * unknown ``dow``/``time_band``/``service`` values are a 422; a weekday
      list is canonicalised (see :func:`canonical_dow`);
    * ``hour`` is 0-23 or an inclusive ``a-b`` range and cannot be combined
      with a ``time_band`` other than ``all``; ``stop`` is a stripped stop_id
      of at most :data:`MAX_STOP_ID_LEN` characters; ``direction`` is 0 or 1.
      Each malformed value is a 422;
    * routes are stripped, de-duplicated preserving order, and capped at
      :data:`MAX_ROUTE_FILTERS` to bound the query and the JSON envelope.
    """
    today = jst_today()

    to_date = _coerce_date(to, "to") or today
    to_date = min(to_date, today)
    from_date = _coerce_date(from_, "from") or (to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    from_date = min(from_date, today)

    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        from_date = to_date - timedelta(days=MAX_RANGE_DAYS - 1)

    # Same reasoning as _coerce_date's type check: `routes` reaches here from
    # stored filter_ctx JSON, so it can be a non-iterable or hold non-strings.
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Iterable):
        raise HTTPException(status_code=422, detail="invalid routes: expected a list of route codes")

    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in routes:
        if not isinstance(raw, str):
            raise HTTPException(status_code=422, detail="invalid routes: expected a list of route codes")
        r = raw.strip()
        if r and r not in seen:
            seen.add(r)
            cleaned.append(r)
        if len(cleaned) >= MAX_ROUTE_FILTERS:
            break

    resolved_time_band = cast(TimeBand, _coerce_enum(time_band, get_args(TimeBand), "time_band"))
    resolved_hour = _coerce_hour(hour)
    if resolved_hour is not None and resolved_time_band != "all":
        raise HTTPException(status_code=422, detail="hour and time_band are mutually exclusive")

    return RangeCtx(
        from_date=from_date,
        to_date=to_date,
        dow=canonical_dow(dow),
        time_band=resolved_time_band,
        service=cast(ServiceType, _coerce_enum(service, get_args(ServiceType), "service")),
        routes=tuple(cleaned),
        hour=resolved_hour,
        stop=_coerce_stop(stop),
        direction=_coerce_direction(direction),
    )


def ctx_payload(ctx: RangeCtx) -> dict[str, Any]:
    """The client-facing JSON projection of a :class:`RangeCtx`.

    Every endpoint that echoes the resolved range back to the caller emits
    exactly these keys, so one frontend reader parses the echo from any of
    them. The dates are the wire-level ``from``/``to`` names, not the
    internal ``from_date``/``to_date`` attributes; ``routes`` is a fresh
    list so a caller can hand the payload to an encoder that mutates it
    without reaching into the frozen context.
    """
    return {
        "from": ctx.from_date.isoformat(),
        "to": ctx.to_date.isoformat(),
        "dow": ctx.dow,
        "time_band": ctx.time_band,
        "service": ctx.service,
        "routes": list(ctx.routes),
        "hour": hour_param(ctx.hour),
        "stop": ctx.stop,
        "dir": ctx.direction,
    }


def hour_param(hour: tuple[int, int] | None) -> str | None:
    """The URL spelling of an hour range: ``"7"`` for one hour, ``"7-9"`` for a range."""
    if hour is None:
        return None
    return str(hour[0]) if hour[0] == hour[1] else f"{hour[0]}-{hour[1]}"


def get_range_ctx(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    dow: str = Query(default="all", description="all, weekday, weekend, or a comma list of mon..sun"),
    time_band: TimeBand = Query(default="all"),
    service: ServiceType = Query(default="all"),
    routes: str | None = Query(default=None, description="Comma-separated route_codes"),
    # Annotated, so a direct Python call without these arguments gets None
    # rather than the Query marker object.
    hour: Annotated[str | None, Query(description="0-23 or an inclusive range a-b; excludes time_band")] = None,
    stop: Annotated[str | None, Query(description="GTFS stop_id")] = None,
    dir_: Annotated[str | None, Query(alias="dir", description="GTFS direction_id, 0 or 1")] = None,
) -> RangeCtx:
    """FastAPI dependency: parse query params into a :class:`RangeCtx`.

    Thin adapter over :func:`clamp_range_ctx` — it only splits the
    comma-separated ``routes`` param; every default, clamp and validation
    rule lives in the shared function.
    """
    return clamp_range_ctx(
        from_=from_,
        to=to,
        dow=dow,
        time_band=time_band,
        service=service,
        routes=routes.split(",") if routes else (),
        hour=hour,
        stop=stop,
        direction=dir_,
    )


def parse_iso_date(s: str | None) -> date | None:
    """Lenient ISO-8601 date parser: ``None``/empty/invalid → ``None``.

    For callers that genuinely have no opinion on a bad value. Request and
    stored-filter boundaries do NOT go through this — they use
    :func:`clamp_range_ctx`, which rejects a malformed date instead of
    quietly substituting a default window.
    """
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def date_range_clause(
    column: str,
    ctx: RangeCtx,
    next_param: int,
) -> tuple[str, list, int]:
    """Inclusive date-range WHERE fragment for an agg table's date ``column``.

    ``column::date`` accepts both ways the agg tables store a date: a DATE
    column, where the cast is a no-op, and ISO date strings in a TEXT column.

    The ``::text`` coercion keeps asyncpg sending the params as TEXT instead
    of trying (and failing) to infer a native type; ``str()`` normalizes the
    mixed caller types (ISO str from the API ctx, datetime.date from tests).
    """
    fragment = f"{column}::date BETWEEN (${next_param}::text)::date AND (${next_param + 1}::text)::date"
    return fragment, [str(ctx.from_date), str(ctx.to_date)], next_param + 2


def dow_clause(
    column: str,
    ctx: RangeCtx,
    next_param: int,
) -> tuple[str, list, int]:
    """``column`` is a date/timestamp column from which to derive day-of-week."""
    days = dow_isodays(ctx.dow)
    if days is None:
        return "TRUE", [], next_param
    if ctx.dow == "weekday":
        return f"EXTRACT(ISODOW FROM {column}::date) BETWEEN 1 AND 5", [], next_param
    # The day numbers come from canonical_dow's closed set, never raw input.
    listed = ", ".join(str(day) for day in sorted(days))
    return f"EXTRACT(ISODOW FROM {column}::date) IN ({listed})", [], next_param


def date_range_clause_ch(ctx: RangeCtx) -> tuple[str, dict]:
    """ClickHouse-dialect counterpart of :func:`date_range_clause` for the
    live `updates` table (ClickHouse's own ``captured_at`` column).

    Buckets by the JST civil day, not UTC — analyze dates every agg_* row on
    the JST calendar, so the same window must select the same days whether it
    is served from an aggregate (`date_range_clause`) or from `updates`.
    ``toDate(captured_at, 'Asia/Tokyo')`` (never a bare ``toDate(captured_at)``)
    matches the same JST-not-UTC translation already proven in
    ``pipeline/db.py::build_dedup_ch_sql``.
    """
    return (
        "toDate(captured_at, 'Asia/Tokyo') >= {ch_from_date:Date} "
        "AND toDate(captured_at, 'Asia/Tokyo') <= {ch_to_date:Date}",
        {"ch_from_date": ctx.from_date, "ch_to_date": ctx.to_date},
    )


def dow_clause_ch(ctx: RangeCtx) -> tuple[str, dict]:
    """ClickHouse-dialect counterpart of :func:`dow_clause`.

    ``toDayOfWeek`` on a JST-shifted ``Date`` (mode 0, the default) returns
    1=Monday..7=Sunday — the same ISODOW numbering Postgres's
    ``EXTRACT(ISODOW FROM ...)`` uses, so the weekday/weekend split matches.
    """
    days = dow_isodays(ctx.dow)
    if days is None:
        return "1", {}
    day_expr = "toDayOfWeek(toDate(captured_at, 'Asia/Tokyo'))"
    if ctx.dow == "weekday":
        return f"{day_expr} BETWEEN 1 AND 5", {}
    listed = ", ".join(str(day) for day in sorted(days))
    return f"{day_expr} IN ({listed})", {}


# ClickHouse expression normalizing `updates.scheduled_time` to a same-day,
# zero-padded "HH:MM". GTFS expresses a trip that continues past midnight as
# an hour >= 24 on the previous service day ("25:30:00" is 01:30 the next
# calendar morning), and a band is a wall-clock window, so the hour must wrap
# modulo 24 before it is compared — otherwise such a trip matches no band at
# all and silently disappears from every time-band-filtered report.
# `toUInt8OrNull` (not `toUInt8`) keeps a malformed or NULL scheduled_time
# from aborting the whole query: it yields NULL, which the comparison then
# excludes, exactly as an unparseable value was excluded before.
_CH_SAME_DAY_HHMM = (
    "concat(leftPad(toString(toUInt8OrNull(substring(scheduled_time, 1, 2)) % 24), 2, '0'), "
    "substring(scheduled_time, 3, 3))"
)


def time_band_clause_ch(ctx: RangeCtx) -> tuple[str, dict]:
    """:func:`time_band_clause_ch_for` for the band named by ``ctx``."""
    return time_band_clause_ch_for(ctx.time_band)


def time_band_clause_ch_for(band: TimeBand) -> tuple[str, dict]:
    """Return a WHERE fragment filtering ClickHouse's ``updates.scheduled_time``
    to the range named by ``band``.

    Takes the band alone rather than a whole :class:`RangeCtx`, so an endpoint
    that accepts a bare ``?time_band=`` without the rest of the range contract
    shares exactly this filter instead of re-deriving the clock bounds.

    ClickHouse's `updates.scheduled_time` is a plain ``Nullable(String)``
    (GTFS ``"HH:MM:SS"`` text — see the migration design doc), not a native
    TIME column, so the comparison is a zero-padded lexicographic string
    range rather than a ``::time`` cast.

    Compares the normalized same-day 5-char ``"HH:MM"`` form of
    `scheduled_time` (:data:`_CH_SAME_DAY_HHMM`), NOT the raw string: every
    static_join agency writes 8-char
    ``"HH:MM:SS"`` (GTFS `departure_time` TEXT), but agency 1 (青森市バス,
    the `aomori_regex` ingest strategy — see
    pipeline/strategies/aomori_regex.py) writes 5-char ``"HH:MM"`` with no
    seconds. Under the old Postgres `TIME` column this didn't matter
    (Postgres normalizes both forms to the same internal value);
    ClickHouse's `String` does not, so a raw compare of ``"09:00"``
    against an 8-char bound like ``"09:00:00"`` is wrong — the 5-char
    form sorts as lexicographically LESS than its own 8-char equivalent,
    so every trip scheduled exactly on a band boundary (05:00, 09:00,
    12:00, 14:00, 17:00, 20:00) would fall into the PREVIOUS band, and
    00:00 departures wouldn't match any band at all. Truncating both
    sides to 5 chars is exact for both ingest-strategy shapes, since a
    zero-padded ``"HH:MM"`` alone is already enough to place a clock time
    within these hour-granularity bands.

    Anything not named in :data:`TIME_BAND_RANGES` — ``"all"``, and any
    value that isn't a band at all — yields the "no time filter" fragment
    rather than raising: ``ctx.time_band`` is typed, but a RangeCtx can also
    be rebuilt from stored client state, and a filter that can't be honoured
    must not turn a read into a 500.
    """
    if band not in TIME_BAND_RANGES:
        return "1", {}
    start, end = TIME_BAND_RANGES[band]
    return (
        f"({_CH_SAME_DAY_HHMM} >= {{ch_tb_start:String}} AND {_CH_SAME_DAY_HHMM} < {{ch_tb_end:String}})",
        {"ch_tb_start": start, "ch_tb_end": end},
    )


def build_updates_filter_ch(ctx: RangeCtx) -> tuple[str, dict]:
    """Combined WHERE fragment for ClickHouse's ``updates`` table.

    Applies date range + DOW + time-band + service + routes filters against
    ClickHouse's `updates` table. Returns a single AND-joined fragment plus a
    ``{name: value}`` dict ready for ``ch.query(..., parameters=...)`` —
    ClickHouse uses named `{name:Type}` parameters, not asyncpg's positional
    ``$N``, so there's no `next_param` to thread; every fragment here uses
    its own unique parameter names.
    """
    parts: list[str] = []
    params: dict = {}

    frag, p = date_range_clause_ch(ctx)
    parts.append(frag)
    params.update(p)

    frag, p = dow_clause_ch(ctx)
    if frag != "1":
        parts.append(frag)
        params.update(p)

    frag, p = time_band_clause_ch(ctx)
    if frag != "1":
        parts.append(frag)
        params.update(p)

    if ctx.service != "all":
        parts.append("service_type = {ch_service:String}")
        params["ch_service"] = ctx.service

    if ctx.routes:
        parts.append("route_code IN {ch_routes:Array(String)}")
        params["ch_routes"] = list(ctx.routes)

    return " AND ".join(parts), params


def time_band_case_sql(column: str) -> str:
    """SQL CASE mapping a TIME column to its `TIME_BAND_RANGES` band key.

    Generated from `TIME_BAND_RANGES` so analyze's bucketing can never drift
    from `time_band_clause_ch`'s filter. NULL / out-of-range -> 'none'.
    """
    arms = "\n".join(
        f"            WHEN {column} >= '{start}'::time AND {column} < '{end}'::time THEN '{band}'"
        for band, (start, end) in TIME_BAND_RANGES.items()
    )
    return f"CASE\n            WHEN {column} IS NULL THEN 'none'\n{arms}\n            ELSE 'none'\n        END"


def build_agg_stop_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for `agg_stop_daily` (date / DOW / service / time_band).

    `time_band` matches the stored band KEY directly (analyze pre-bucketed it
    via `time_band_case_sql`), not a scheduled_time range.
    """
    parts: list[str] = []
    params: list = []
    n = next_param

    frag, p, n = date_range_clause("date", ctx, n)
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

    frag, p, n = date_range_clause("date", ctx, n)
    parts.append(frag)
    params.extend(p)

    frag, p, n = dow_clause("date", ctx, n)
    if frag != "TRUE":
        parts.append(frag)
        params.extend(p)

    return " AND ".join(parts), params, n
