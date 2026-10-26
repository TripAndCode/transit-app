"""The observed route codes a route list leaves out by default, by reason.

A timetable change renumbers routes, so a window that spans one carries the
old codes for its first days. Ranked against routes observed every day, a
retired code with a bad week leads the list under a bare id no route name
can be resolved for. :func:`retired_route_codes` names those codes.

Some feeds echo the timetable for a route instead of reporting its delay, so
every departure reads exactly on time. Such a route says nothing about
punctuality, yet it would top every punctuality list.
:func:`zero_delay_route_codes` names those codes.

:func:`route_list` leaves both out of a list unless asked and says how many
it left, so a route list can offer each back.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any

from api.range import RangeCtx
from pipeline.cache import async_lru_cache
from pipeline.histogram import bucketize

# The route_code a static route's id maps to. Aomori's ids carry the code in a
# parenthesised tail ('国道・古川線(1021)'); every other feed's id is its code,
# which the replace leaves as it is.
ROUTE_CODE_SQL = "regexp_replace({route_id}, '.*\\((\\d+)\\)$', '\\1')"

# How recently, before the agency's last observed day in the window, a route
# must have been seen to count as still running.
RECENT_DAYS = 7

# How many days, through the agency's last observed day in the window, a
# route's delays are judged over when the window itself is shorter. A day or a
# week of zeros from a route that reports delays is ordinary.
ZERO_DELAY_DAYS = 28
# The fewest observations a route needs before its zeros say its feed reports
# no delay. Feeds that report delay in whole minutes read a punctual departure
# as exactly 0, so a few dozen zeros in a row from a route that does report
# delays are chance; a hundred are not.
ZERO_DELAY_MIN_SAMPLES = 100

# The reasons a route list leaves a code out, each with its own toggle.
RETIRED = "retired"
ZERO_DELAY = "zero_delay"


def route_code_sql(route_id_column: str) -> str:
    """The SQL expression for ``route_id_column``'s route_code."""
    return ROUTE_CODE_SQL.format(route_id=route_id_column)


async def retired_route_codes(conn, agency_id: int, ctx: RangeCtx) -> frozenset[str]:
    """Route codes observed in ``ctx`` that the current timetable lacks and
    that went unobserved over the last :data:`RECENT_DAYS` of the agency's data.

    The recent days end on the agency's last observed day in the window, not on
    ``ctx.to_date``: an agency whose data stops early would otherwise have
    every route look retired. An agency with no timetable loaded retires
    nothing, since there is nothing to compare against.

    Only the window's dates decide the set, so the cache is keyed on them alone
    and every route list over one window shares it.
    """
    return await _retired_between(conn, agency_id, ctx.from_date, ctx.to_date)


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def _retired_between(conn, agency_id: int, from_date: date, to_date: date) -> frozenset[str]:
    rows = await conn.fetch(
        "WITH seen AS ("
        "  SELECT route_code, max(date) AS last_seen FROM agg_route_daily"
        "  WHERE agency_id = $1 AND date >= $2 AND date <= $3"
        "  GROUP BY route_code"
        "), timetable AS ("
        f"  SELECT DISTINCT {route_code_sql('route_id')} AS route_code"
        "  FROM static_routes WHERE agency_id = $1"
        ") "
        "SELECT s.route_code FROM seen s "
        "WHERE EXISTS (SELECT 1 FROM timetable) "
        "  AND s.route_code NOT IN (SELECT route_code FROM timetable) "
        "  AND s.last_seen <= (SELECT max(last_seen) FROM seen) - $4::int",
        agency_id,
        from_date,
        to_date,
        RECENT_DAYS,
    )
    return frozenset(r["route_code"] for r in rows)


def invalidate_retired_routes() -> None:
    """Forget every cached retired set, after a load replaced an agency's
    timetable, so the next route list judges codes against the new one."""
    _retired_between.cache_clear()  # type: ignore[attr-defined]


async def zero_delay_route_codes(conn, agency_id: int, ctx: RangeCtx) -> frozenset[str]:
    """Route codes whose every observation reads exactly 0 seconds of delay,
    over at least :data:`ZERO_DELAY_MIN_SAMPLES` observations.

    The days judged are the window's own through the agency's last observed
    day in it, stretched back to :data:`ZERO_DELAY_DAYS` days when the window
    is shorter. So a flagged route reads 0 throughout the window, and a short
    window does not flag a route for a quiet day or week. Codes are judged per
    agency: one code can be zero under one agency's feed and delayed under
    another's.

    Only the window's dates decide the set, so the cache is keyed on them alone
    and every route list over one window shares it.
    """
    return await _zero_delay_between(conn, agency_id, ctx.from_date, ctx.to_date)


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def _zero_delay_between(conn, agency_id: int, from_date: date, to_date: date) -> frozenset[str]:
    # Every observation in the histogram bucket 0 opens, which holds no early
    # departure, and a sum of 0 leave only exact zeros. The sum alone would
    # pass early and late departures that cancel out.
    rows = await conn.fetch(
        "WITH last AS ("
        "  SELECT max(date) AS day FROM agg_route_daily_dist"
        "  WHERE agency_id = $1 AND date >= $2 AND date <= $3"
        ") "
        "SELECT d.route_code FROM agg_route_daily_dist d, last "
        "WHERE d.agency_id = $1 AND d.date <= last.day AND d.date >= least($2, last.day - $4::int) "
        "GROUP BY d.route_code "
        "HAVING sum(d.samples) >= $5 AND sum(d.sum_delay_sec) = 0 "
        "  AND sum(d.hist[$6::int]) = sum(d.samples)",
        agency_id,
        from_date,
        to_date,
        ZERO_DELAY_DAYS - 1,
        ZERO_DELAY_MIN_SAMPLES,
        bucketize(0) + 1,
    )
    return frozenset(r["route_code"] for r in rows)


async def left_out_route_codes(conn, agency_id: int, ctx: RangeCtx) -> dict[str, frozenset[str]]:
    """The codes a route list over ``ctx`` leaves out by default, by reason.

    Each code has one reason, retired first, so a list counts it once and one
    toggle brings it back.
    """
    retired = await retired_route_codes(conn, agency_id, ctx)
    zero_delay = await zero_delay_route_codes(conn, agency_id, ctx)
    return {RETIRED: retired, ZERO_DELAY: zero_delay - retired}


def route_list(
    rows: Sequence[Sequence[Any]],
    n: int,
    left_out: Mapping[str, frozenset[str]],
    kept: Collection[str],
) -> tuple[list, dict[str, int]]:
    """The ranked ``rows`` through the ``n``-th whose route code (element 0) no
    reason leaves out, without the left-out rows whose reason is not ``kept``,
    and how many of the left-out rows among them each reason holds.

    Every choice of kept reasons lists the same other routes, and keeping a
    reason adds exactly the rows its count names: the ones ranked among them,
    never one a routes filter or a floor already left out. Kept rows come on
    top of ``n``, and the counts are the same whichever reasons are kept.
    """
    held = frozenset().union(*left_out.values())
    prefix: list = []
    listed = 0
    for r in rows:
        if listed == n:
            break
        prefix.append(r)
        if r[0] not in held:
            listed += 1
    counts = {reason: sum(1 for r in prefix if r[0] in codes) for reason, codes in left_out.items()}
    dropped = _dropped(left_out, kept)
    return [r for r in prefix if r[0] not in dropped], counts


def listed_count(rows: Sequence[Sequence[Any]], left_out: Mapping[str, frozenset[str]], kept: Collection[str]) -> int:
    """How many of ``rows`` a list keeps, ignoring its length."""
    dropped = _dropped(left_out, kept)
    return sum(1 for r in rows if r[0] not in dropped)


def _dropped(left_out: Mapping[str, frozenset[str]], kept: Collection[str]) -> frozenset[str]:
    return frozenset().union(*(codes for reason, codes in left_out.items() if reason not in kept))
