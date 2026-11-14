"""Aggregates computed and stored in ClickHouse.

Three kinds of table live here. The date-keyed aggregates (TABLES) are built
from `updates` by a ClickHouse GROUP BY; STATIC_JOINED_TABLES also join each
row to the archived timetable its version name maps to (`ScheduleJoin`), the
loaded one when it maps to none. INTERNAL_TABLES,
also date-keyed and built the same way, exist only in ClickHouse: COUNTS holds
the exact delay distribution, ROUTE_STOP_DAYS the (stop, route) pairs each date
observed. The whole-history aggregates (WHOLE_HISTORY_TABLES) are finalized
from them. Each
table in COPIED_TABLES has the name and columns of a Postgres counterpart;
`pipeline.analyze` drives the builds and copies the agency's rows of each into
the Postgres table the API reads. This module is the ClickHouse side only.

A run replaces whole months of service date in the date-keyed tables: each is
partitioned by (agency_id, month), a run assembles each month it touches in the
agency's work table, recomputing only the dates whose rows changed from one dedup
of the month that the tables needing it share, and `swap` then moves each month
into place with REPLACE PARTITION, so a reader never sees a month half built.
The internal tables are among them; the whole-history tables are finalized from
them into one partition per agency, which `swap_whole_history` replaces whole.

Every copied table's value equals what the Postgres builder it replaced produced,
column for column; tests/pipeline/ch_parity.py keeps those builders as the parity
oracle. The one departure from them is on purpose: a departure scheduled at
24:00:00 or later, which they left out of every hour and band, is filed under
its hour of the service day and its wall-clock band (api.range.scheduled_hour_ch,
api.range.time_band_ch).
Means are rounded half away from zero from exact integer sums, which is what
Postgres's ROUND(numeric) does; ClickHouse's round() on a float rounds half to
even and is never used for them.
"""

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Set
from contextlib import AbstractContextManager, nullcontext
from datetime import date, timedelta
from typing import NamedTuple

from api.range import scheduled_hour_ch, scheduled_sec_ch, time_band_ch
from pipeline import static_archive
from pipeline.clickhouse import UPDATES_TABLE
from pipeline.db import build_dedup_ch_sql
from pipeline.histogram import (
    HI,
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    LO,
    N_BUCKETS,
    WIDTH,
)
from pipeline.service_day import filed_capture_span, service_date_ch
from pipeline.static_archive import ScheduleJoin, Stamp, observation_rows

logger = logging.getLogger(__name__)


def months_of(dates: Iterable[date]) -> list[int]:
    """The months holding *dates*, as ascending YYYYMM integers (`toYYYYMM`'s form)."""
    return sorted({d.year * 100 + d.month for d in dates})


def version_names(
    ch,
    agency_id: int,
    where: str,
    params: dict,
    join: ScheduleJoin | None = None,
    stamp: Stamp = Stamp.INGESTED,
    between: tuple[date, date] | None = None,
) -> dict[str, int]:
    """Each version name the agency's `updates` rows matching *where* are filed
    under ('' for none), as *stamp* files them, with its row count. A *stamp*
    other than INGESTED reads *join*'s versions in force over *between*, the
    dates *where* can reach (`ScheduleJoin.force_params`). REFIT files a row
    under the same name IN_FORCE does, so it is read as IN_FORCE, without the
    schedule archive's join."""
    stamp = Stamp.IN_FORCE if stamp is Stamp.REFIT else stamp
    rows = ch.query(
        "SELECT ifNull(u.static_version_id, '') AS stem, count() "
        f"{observation_rows(f'u.agency_id = {{agency_id:UInt16}} AND ({where})', stamp)} GROUP BY stem",
        parameters={"agency_id": agency_id, **params, **_filing_params(join, stamp, between=between)},
    ).result_rows
    return dict(rows)


def _filing_params(
    join: ScheduleJoin | None,
    stamp: Stamp,
    present: Iterable[str] | None = None,
    between: tuple[date, date] | None = None,
) -> dict:
    if stamp is Stamp.INGESTED:
        return {}
    if join is None:
        raise ValueError(f"reading rows {stamp.value} needs a ScheduleJoin")
    return join.params(present, between)


def month_span(month: int, days: list[date] | None = None) -> tuple[date, date]:
    """The first and last service dates a read of *month*, or of only *days* within it, reaches."""
    if days:
        return min(days), max(days)
    first, following = month_bounds(month)
    return first, following - timedelta(days=1)


def stamp_for(join: ScheduleJoin | None, refit: Set[date] | None, dates: Iterable[date]) -> Stamp:
    """How a read of *dates* files its rows: INGESTED without versions in
    force, else REFIT where *refit*, the dates whose rows need the archive's
    schedule (static_archive.refit_dates), names one of them or is unknown
    (None), else IN_FORCE."""
    if join is None or not join.force:
        return Stamp.INGESTED
    if refit is None or not refit.isdisjoint(dates):
        return Stamp.REFIT
    return Stamp.IN_FORCE


def month_bounds(month: int) -> tuple[date, date]:
    """YYYYMM *month* as [its first day, the next month's first day)."""
    first = date(month // 100, month % 100, 1)
    return first, (first + timedelta(days=32)).replace(day=1)


def month_where(month: int) -> tuple[str, dict]:
    """A predicate selecting the `updates` rows filed under a service date of
    *month* (`static_archive.FILED_DATE_SQL`).

    With a range on the stored instant, the span those rows are captured in
    (`filed_capture_span`), so the (agency_id, captured_at) sort key prunes the
    scan to the month. The bounds bind as untyped DateTime64: clickhouse-connect
    renders an aware datetime in the server's timezone, which is the zone an
    untyped value is read in.
    """
    first, following = month_bounds(month)
    last = following - timedelta(days=1)
    start, end = filed_capture_span(first, last)
    return (
        "u.captured_at >= {month_start:DateTime64} AND u.captured_at < {month_end:DateTime64} "
        f"AND {static_archive.FILED_DATE_SQL} BETWEEN {{month_first:Date}} AND {{month_last:Date}}",
        {"month_start": start, "month_end": end, "month_first": first, "month_last": last},
    )


def _scope_where(month: int, scopes: Iterable[list[date] | None]) -> tuple[str, dict]:
    """The predicate for the union of *scopes* within *month*: the whole month when any is None."""
    scopes = list(scopes)
    if any(scope is None for scope in scopes):
        return month_where(month)
    return days_where(sorted({day for scope in scopes for day in scope or ()}))


def days_where(days: list[date]) -> tuple[str, dict]:
    """A predicate selecting the `updates` rows filed under one of *days*.

    With ranges on the stored instant, one per run of consecutive days, for
    the reason `month_where` gives.
    """
    clauses: list[str] = []
    params: dict = {"filed_days": sorted(set(days))}
    for i, (first, last) in enumerate(_runs(days)):
        clauses.append(
            f"(u.captured_at >= {{day_start_{i}:DateTime64}} AND u.captured_at < {{day_end_{i}:DateTime64}})"
        )
        params[f"day_start_{i}"], params[f"day_end_{i}"] = filed_capture_span(first, last)
    if not clauses:
        return "0", {}
    return f"({' OR '.join(clauses)}) AND has({{filed_days:Array(Date)}}, {static_archive.FILED_DATE_SQL})", params


def _runs(days: Iterable[date]) -> list[tuple[date, date]]:
    """*days* as runs of consecutive dates, each its first and last."""
    runs: list[tuple[date, date]] = []
    for day in sorted(set(days)):
        if runs and runs[-1][1] + timedelta(days=1) == day:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def _round_half_away(numerator: str, denominator: str, scale: int) -> str:
    """SQL for *numerator* / *denominator* rounded half away from zero to *scale* places.

    Integer arithmetic throughout, so no float ever holds the value. Both
    arguments are integer expressions and the denominator is positive. The result
    is a Decimal64 at *scale*, or an integer at scale 0.
    """
    unit = 10**scale
    quotient = f"sign({numerator}) * intDiv(2 * abs({numerator}) * {unit} + ({denominator}), 2 * ({denominator}))"
    if scale == 0:
        return f"({quotient})"
    return f"(toDecimal64({quotient}, 0) * toDecimal64(1, {scale}) / {unit})"


# The route-keyed tables skip a row with no route and file a NULL service type
# under '', grouping by that same expression so NULL and '' never split a key.
_ROUTE_KEYS = "d.date, d.route_code, ifNull(d.service_type, '')"


def _daily_trend(source: str) -> str:
    avg_min = _round_half_away("s", "60 * n", 2)
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, route_code, service_type,
               toFloat32({avg_min}) AS avg_min, toInt32(n) AS samples,
               s AS sum_delay_sec, late AS sum_late_sec
        FROM (
            SELECT d.date AS date, assumeNotNull(d.route_code) AS route_code,
                   ifNull(d.service_type, '') AS service_type,
                   sum(assumeNotNull(d.dep_delay)) AS s, count() AS n,
                   sum(greatest(assumeNotNull(d.dep_delay), 0)) AS late
            FROM ({source}) AS d
            WHERE d.route_code IS NOT NULL
            GROUP BY {_ROUTE_KEYS}
        )
    """


def _route_daily(source: str) -> str:
    avg_delay_sec = _round_half_away("s", "n", 0)
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, route_code, service_type,
               toInt32({avg_delay_sec}) AS avg_delay_sec, worst AS worst_delay_sec,
               toInt32(trips) AS trips_observed, toInt32(n) AS samples,
               last_seen AS last_seen_at, s AS sum_delay_sec
        FROM (
            SELECT d.date AS date, assumeNotNull(d.route_code) AS route_code,
                   ifNull(d.service_type, '') AS service_type,
                   sum(assumeNotNull(d.dep_delay)) AS s, count() AS n,
                   max(assumeNotNull(d.dep_delay)) AS worst, uniqExact(d.trip_id) AS trips,
                   max(d.last_captured_at) AS last_seen
            FROM ({source}) AS d
            WHERE d.route_code IS NOT NULL
            GROUP BY {_ROUTE_KEYS}
        )
    """


# histogram.bucketize in SQL: underflow below LO, overflow at or past HI, and
# WIDTH-second inner bins between, indexed with intDiv on a non-negative offset.
_BUCKET = f"multiIf(dep < {LO}, 0, dep >= {HI}, {N_BUCKETS - 1}, 1 + intDiv(dep - ({LO}), {WIDTH}))"
_HIST = "[" + ", ".join(f"toInt32(countIf(d.b = {i}))" for i in range(N_BUCKETS)) + "]"


def _route_daily_dist(source: str) -> str:
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, route_code, service_type,
               toInt32(n) AS samples, s AS sum_delay_sec,
               toInt32(on_time) AS on_time_count, toInt32(late5) AS late5_count, hist
        FROM (
            SELECT d.date AS date, assumeNotNull(d.route_code) AS route_code,
                   ifNull(d.service_type, '') AS service_type,
                   count() AS n, sum(d.dep) AS s,
                   countIf(d.dep <= {LEGACY_ON_TIME_LATE_TOLERANCE_SEC}) AS on_time,
                   countIf(d.dep > {LEGACY_SEVERE_LATE_TOLERANCE_SEC}) AS late5,
                   {_HIST} AS hist
            FROM (SELECT *, assumeNotNull(dep_delay) AS dep, {_BUCKET} AS b FROM ({source})) AS d
            WHERE d.route_code IS NOT NULL
            GROUP BY {_ROUTE_KEYS}
        )
    """


# The departure's hour on its service day's clock (api.range.scheduled_hour_ch):
# a departure at 24:10:00 is hour 24 of the service day it belongs to.
_SEC = scheduled_sec_ch("d.")
_HOUR = f"toInt16(assumeNotNull({scheduled_hour_ch('d.')}))"


def _hour_daily(source: str) -> str:
    avg_min = _round_half_away("s", "60 * n", 2)
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, hour, {avg_min} AS avg_min,
               toInt32(n) AS samples, s AS sum_delay_sec
        FROM (
            SELECT d.date AS date, {_HOUR} AS hour, sum(assumeNotNull(d.dep_delay)) AS s, count() AS n
            FROM ({source}) AS d
            WHERE {_SEC} IS NOT NULL
            GROUP BY d.date, {_HOUR}
        )
    """


def _route_delay_counts(source: str) -> str:
    # The whole-history tables key on a typed service, so an untyped or unrouted
    # event is left out here, as the alltime slice left it out; filtering after the
    # dedup is the same because both columns are part of its key.
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, route_code, service_type, scheduled_sec,
               dep_delay, toInt32(n) AS samples, toInt64(dep_delay) * toInt64(n) AS sum_delay_sec
        FROM (
            SELECT d.date AS date, assumeNotNull(d.route_code) AS route_code,
                   assumeNotNull(d.service_type) AS service_type, {_SEC} AS scheduled_sec,
                   assumeNotNull(d.dep_delay) AS dep_delay, count() AS n
            FROM ({source}) AS d
            WHERE d.route_code IS NOT NULL AND d.service_type IS NOT NULL
            GROUP BY date, route_code, service_type, scheduled_sec, dep_delay
        )
    """


# The band of the departure's time on the wall clock (api.range.time_band_ch),
# 'none' for no scheduled time.
_TIME_BAND = time_band_ch("d.")


# Each row's timetable (static_archive.JOIN_CONTENT_SQL), computed once per row before the join.
def _with_join_content(source: str) -> str:
    return f"SELECT *, {static_archive.JOIN_CONTENT_SQL} AS join_content FROM ({source})"


# The archived stop times of the contents the rows join, on the stop event's trip and sequence.
_STATIC_JOIN = (
    f"INNER JOIN {static_archive.archived_stop_times_sql('content_hash, trip_id, stop_sequence, stop_id')} AS st "
    "ON st.content_hash = d.join_content AND st.trip_id = d.trip_id "
    "AND st.stop_sequence = toInt32(d.stop_sequence)"
)


def _stop_daily(source: str) -> str:
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, stop_id, date, service_type, time_band,
               s AS delay_sum, toInt32(n) AS samples
        FROM (
            SELECT st.stop_id AS stop_id, d.date AS date, ifNull(d.service_type, '') AS service_type,
                   {_TIME_BAND} AS time_band, sum(assumeNotNull(d.dep_delay)) AS s, count() AS n
            FROM ({_with_join_content(source)}) AS d
            {_STATIC_JOIN}
            GROUP BY stop_id, date, service_type, time_band
        )
    """


def _route_stop_daily(source: str) -> str:
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, route_code, stop_id, date, service_type, time_band,
               s AS delay_sum, toInt64(n) AS samples
        FROM (
            SELECT assumeNotNull(d.route_code) AS route_code, st.stop_id AS stop_id, d.date AS date,
                   ifNull(d.service_type, '') AS service_type, {_TIME_BAND} AS time_band,
                   sum(assumeNotNull(d.dep_delay)) AS s, count() AS n
            FROM ({_with_join_content(source)}) AS d
            {_STATIC_JOIN}
            WHERE d.route_code IS NOT NULL
            GROUP BY route_code, stop_id, date, service_type, time_band
        )
    """


def _stop_route_days(source: str) -> str:
    """Each (service date, stop, route) a poll reports, read from every row
    (`_raw_select`), untimely ones too (`pipeline.db.timely_reading_ch`): it
    records which routes serve a stop, not a figure of its stop events, so a
    trip the feed lists long after it ran still names a route that serves it."""
    return f"""
        SELECT DISTINCT toInt32({{agency_id:UInt16}}) AS agency_id, d.date AS date, st.stop_id AS stop_id,
               assumeNotNull(d.route_code) AS route_code
        FROM ({_with_join_content(source)}) AS d
        {_STATIC_JOIN}
        WHERE d.route_code IS NOT NULL
    """


def _pctl(p: str) -> str:
    """Postgres's PERCENTILE_DISC(*p*) over `dist`, sorted (value, count) pairs whose counts sum to `n`.

    The first value whose running count reaches ceil(p * n), with p * n in float64
    as Postgres computes it (`Max(1, ceil(p * rowcount))`), so equal to its
    arraySort(groupArray(x))[ceil(p * n)] form without holding every value.
    """
    position = f"toUInt64(ceil({p} * n))"
    running = "arrayCumSum(arrayMap(x -> tupleElement(x, 2), dist))"
    return f"tupleElement(dist[arrayFirstIndex(c -> c >= {position}, {running})], 1)"


def _distribution(source: str, keys: str, where: str = "1") -> str:
    """Per *keys*: the event count `n`, the delay sum `s`, the on-time and late
    counts, and `dist`, every delay value with its count, sorted by value.

    The counts are summed per (*keys*, value) first, so `dist` holds each value
    once: its length is bounded by the delay range, not by how many scheduled
    times a route has."""
    return f"""
        SELECT {keys}, sum(k) AS n, sum(toInt64(dep) * k) AS s,
               sumIf(k, dep <= {LEGACY_ON_TIME_LATE_TOLERANCE_SEC}) AS on_time,
               sumIf(k, dep > {LEGACY_SEVERE_LATE_TOLERANCE_SEC}) AS late5,
               arraySort(groupArray((dep, k))) AS dist
        FROM (
            SELECT {keys}, dep, sum(samples) AS k
            FROM (
                SELECT v.route_code AS route_code, v.service_type AS service_type,
                       v.scheduled_sec AS scheduled_sec, v.dep_delay AS dep, v.samples AS samples
                FROM ({source}) AS v
                WHERE {where}
            )
            GROUP BY {keys}, dep
        )
        GROUP BY {keys}
    """


def _route_stats(source: str) -> str:
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, route_code, service_type,
               toFloat32({_round_half_away("s", "60 * n", 2)}) AS avg_min,
               toFloat32({_round_half_away("p50", "60", 2)}) AS p50_min,
               toFloat32({_round_half_away("p90", "60", 2)}) AS p90_min,
               toInt32(late5) AS late_5min_plus,
               toFloat32({_round_half_away("100 * on_time", "n", 1)}) AS on_time_pct,
               toFloat32({_round_half_away("100 * late5", "n", 1)}) AS late5_pct,
               toInt32(n) AS samples, s AS sum_delay_sec
        FROM (
            SELECT *, {_pctl("0.5")} AS p50, {_pctl("0.9")} AS p90
            FROM ({_distribution(source, "route_code, service_type")})
        )
    """


# Seconds from the service day's 00:00 as HH:MM:SS, the hours running past 23
# for a departure after midnight: the text form of the Postgres `interval`
# agg_route_hour keeps.
_SCHEDULED_TIME = (
    "concat(leftPad(toString(intDiv(sec, 3600)), 2, '0'), ':', "
    "leftPad(toString(intDiv(sec, 60) % 60), 2, '0'), ':', leftPad(toString(sec % 60), 2, '0'))"
)


def _route_hour(source: str) -> str:
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, route_code, service_type,
               {_SCHEDULED_TIME} AS scheduled_time,
               toFloat32({_round_half_away("s", "60 * n", 2)}) AS avg_min,
               toFloat32({_round_half_away("p50", "60", 2)}) AS p50_min,
               toFloat32({_round_half_away("p90", "60", 2)}) AS p90_min,
               toInt32(n) AS samples, s AS sum_delay_sec
        FROM (
            SELECT *, assumeNotNull(scheduled_sec) AS sec, {_pctl("0.5")} AS p50, {_pctl("0.9")} AS p90
            FROM ({_distribution(source, "route_code, service_type, scheduled_sec", "v.scheduled_sec IS NOT NULL")})
        )
    """


def _stop_routes(source: str) -> str:
    """Each stop's routes over its whole history, from stop_route_days: which
    routes serve it, untimely readings included, as that table records them."""
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, stop_id,
               arrayStringConcat(arraySort(groupUniqArray(route_code)), ',') AS route_codes
        FROM ({source})
        GROUP BY stop_id
    """


def _route_hour_dow(source: str) -> str:
    avg_min = _round_half_away("s", "60 * n", 2)
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, route_code, service_type, dow, hour,
               toFloat32({avg_min}) AS avg_min, toInt32(n) AS samples, s AS sum_delay_sec
        FROM (
            SELECT v.route_code AS route_code, v.service_type AS service_type,
                   toInt16(toDayOfWeek(v.date)) AS dow, toInt16(intDiv(assumeNotNull(v.scheduled_sec), 3600)) AS hour,
                   sum(v.samples) AS n, sum(v.sum_delay_sec) AS s
            FROM ({source}) AS v
            WHERE v.scheduled_sec IS NOT NULL
            GROUP BY route_code, service_type, dow, hour
        )
    """


# Two (ClickHouse, Postgres) integer expressions over the agency's rows: with the
# row count, what a store check compares between ClickHouse and the Postgres copy.
_Figures = tuple[tuple[str, str], tuple[str, str]]
_DEFAULT_FIGURES: _Figures = (("sum(samples)", "sum(samples)"), ("sum(sum_delay_sec)", "sum(sum_delay_sec)"))
_DELAY_SUM_FIGURES: _Figures = (("sum(samples)", "sum(samples)"), ("sum(delay_sum)", "sum(delay_sum)"))


class _Spec(NamedTuple):
    columns: tuple[str, ...]
    select: Callable[[str], str]
    figures: _Figures = _DEFAULT_FIGURES
    # Read from every `updates` row (`_raw_select`) rather than from the dedup.
    raw: bool = False
    # Reads the dedup's `scheduled_sec`, which alone holds a time past 24:00.
    scheduled_sec: bool = False


class _WholeSpec(NamedTuple):
    columns: tuple[str, ...]
    select: Callable[[str], str]
    source: str  # the internal table it is finalized from
    figures: _Figures = _DEFAULT_FIGURES


# Build and swap order. Each copied table's columns are its Postgres table's, in
# the order the copy writes them; the internal tables have no Postgres table.
_SPECS: dict[str, _Spec] = {
    "agg_daily_trend": _Spec(
        ("agency_id", "date", "route_code", "service_type", "avg_min", "samples", "sum_delay_sec", "sum_late_sec"),
        _daily_trend,
    ),
    "agg_route_daily": _Spec(
        (
            "agency_id",
            "date",
            "route_code",
            "service_type",
            "avg_delay_sec",
            "worst_delay_sec",
            "trips_observed",
            "samples",
            "last_seen_at",
            "sum_delay_sec",
        ),
        _route_daily,
    ),
    "agg_route_daily_dist": _Spec(
        (
            "agency_id",
            "date",
            "route_code",
            "service_type",
            "samples",
            "sum_delay_sec",
            "on_time_count",
            "late5_count",
            "hist",
        ),
        _route_daily_dist,
    ),
    "agg_hour_daily": _Spec(
        ("agency_id", "date", "hour", "avg_min", "samples", "sum_delay_sec"), _hour_daily, scheduled_sec=True
    ),
    "agg_stop_daily": _Spec(
        ("agency_id", "stop_id", "date", "service_type", "time_band", "delay_sum", "samples"),
        _stop_daily,
        _DELAY_SUM_FIGURES,
        scheduled_sec=True,
    ),
    "agg_route_stop_daily": _Spec(
        ("agency_id", "route_code", "stop_id", "date", "service_type", "time_band", "delay_sum", "samples"),
        _route_stop_daily,
        _DELAY_SUM_FIGURES,
        scheduled_sec=True,
    ),
    "route_delay_counts": _Spec(
        (
            "agency_id",
            "date",
            "route_code",
            "service_type",
            "scheduled_sec",
            "dep_delay",
            "samples",
            "sum_delay_sec",
        ),
        _route_delay_counts,
        scheduled_sec=True,
    ),
    "stop_route_days": _Spec(("agency_id", "date", "stop_id", "route_code"), _stop_route_days, raw=True),
}
COUNTS = "route_delay_counts"
ROUTE_STOP_DAYS = "stop_route_days"
# The date-keyed tables that exist only in ClickHouse, which the whole-history tables are finalized from.
INTERNAL_TABLES: tuple[str, ...] = (COUNTS, ROUTE_STOP_DAYS)
# The date-keyed tables copied into Postgres; `build` fills these and INTERNAL_TABLES month by month.
TABLES: tuple[str, ...] = tuple(name for name in _SPECS if name not in INTERNAL_TABLES)
MONTHLY_TABLES: tuple[str, ...] = (*TABLES, *INTERNAL_TABLES)
# The tables whose rows join the timetable archive by each row's version, so a build of one needs a ScheduleJoin.
STATIC_JOINED_TABLES: frozenset[str] = frozenset(
    {"agg_stop_daily", "agg_route_stop_daily", ROUTE_STOP_DAYS, "agg_stop_routes"}
)

# Whole-history tables, finalized from their internal table by `build_whole_history`. Build and swap order.
_WHOLE_SPECS: dict[str, _WholeSpec] = {
    "agg_route_stats": _WholeSpec(
        (
            "agency_id",
            "route_code",
            "service_type",
            "avg_min",
            "p50_min",
            "p90_min",
            "late_5min_plus",
            "on_time_pct",
            "late5_pct",
            "samples",
            "sum_delay_sec",
        ),
        _route_stats,
        COUNTS,
    ),
    "agg_route_hour": _WholeSpec(
        (
            "agency_id",
            "route_code",
            "service_type",
            "scheduled_time",
            "avg_min",
            "p50_min",
            "p90_min",
            "samples",
            "sum_delay_sec",
        ),
        _route_hour,
        COUNTS,
    ),
    "agg_route_hour_dow": _WholeSpec(
        ("agency_id", "route_code", "service_type", "dow", "hour", "avg_min", "samples", "sum_delay_sec"),
        _route_hour_dow,
        COUNTS,
    ),
    "agg_stop_routes": _WholeSpec(
        ("agency_id", "stop_id", "route_codes"),
        _stop_routes,
        ROUTE_STOP_DAYS,
        # Order-independent, so ClickHouse's byte order and the copy's collation order agree.
        (("sum(lengthUTF8(route_codes))", "sum(char_length(route_codes))"), ("0", "0")),
    ),
}
WHOLE_HISTORY_TABLES: tuple[str, ...] = tuple(_WHOLE_SPECS)
COPIED_TABLES: tuple[str, ...] = (*TABLES, *WHOLE_HISTORY_TABLES)
ALL_TABLES: tuple[str, ...] = (*MONTHLY_TABLES, *WHOLE_HISTORY_TABLES)
# Every persistent ClickHouse table besides the observation ones (`updates`,
# `updates_live`): what analyze builds and the timetable archive. db/clickhouse/schema.sql
# creates exactly these and the observation tables.
STORE_TABLES: tuple[str, ...] = (
    *ALL_TABLES,
    static_archive.ARCHIVE,
    static_archive.SCHEDULE,
    static_archive.VERSION_MAP,
    static_archive.SPANS,
    static_archive.CALENDAR,
)
_ALL_SPECS: dict[str, _Spec | _WholeSpec] = {**_SPECS, **_WHOLE_SPECS}
COLUMNS: dict[str, tuple[str, ...]] = {name: spec.columns for name, spec in _ALL_SPECS.items()}


def _checked(table: str) -> str:
    if table not in COLUMNS:
        raise ValueError(f"Not a ClickHouse aggregate: {table!r}")
    return table


# A ClickHouse regular expression matching every name work_table and dedup_table
# return, and the timetable archive's work tables; the test fixtures find every
# agency's scratch tables by it.
WORK_TABLE_PATTERN = "__work_a[0-9]+$"


def work_table(table: str, agency_id: int) -> str:
    """The agency's scratch copy of *table*.

    Per agency, because the agency lock serializes only that agency's runs:
    another agency's run may be building its own copy at the same time.
    """
    return f"{_checked(table)}__work_a{int(agency_id)}"


def _dedup_select(where: str, *, scheduled_sec: bool = True, stamp: Stamp = Stamp.INGESTED) -> str:
    """The dedup of the agency's `updates` rows matching *where*, with every column a table reads.

    Without *scheduled_sec*, every column but `scheduled_sec`: what a table
    whose `_Spec` does not read it dedups for itself."""
    return build_dedup_ch_sql(
        extra_where=where,
        include_captured_at=True,
        include_scheduled_sec=scheduled_sec,
        include_static_version=True,
        stamp=stamp,
    )


def _raw_select(where: str, stamp: Stamp = Stamp.INGESTED) -> str:
    """The agency's `updates` rows matching *where*, with no dedup: every poll, delay or not."""
    return (
        f"SELECT {static_archive.filed_date_sql(stamp)} AS date, u.route_code AS route_code, u.trip_id AS trip_id, "
        f"u.stop_sequence AS stop_sequence, ifNull(u.static_version_id, '') AS static_version_id "
        f"{observation_rows(f'u.agency_id = {{agency_id:UInt16}} AND ({where})', stamp)}"
    )


def select_sql(
    table: str,
    month: int,
    days: list[date] | None = None,
    source: str | None = None,
    *,
    join: ScheduleJoin | None = None,
    present: Iterable[str] | None = None,
    stamp: Stamp = Stamp.INGESTED,
) -> tuple[str, dict]:
    """The SELECT computing *table*'s rows for the agency bound as `agency_id`:
    those of one month, or of only *days* within it.

    Its columns are COLUMNS[table], in order. Without *source* it dedups
    `updates` itself: what a read-only session runs, and what `build` runs for a
    month only one table needs. *source* names a table already holding the
    month's dedup (`build`'s dedup table), which it reads instead. A raw table
    reads `updates` without the dedup, so it takes no *source*.

    A table in STATIC_JOINED_TABLES also binds *join*'s parameters, narrowed to
    the contents the version names in *present* join (`ScheduleJoin.params`).
    Without *source*, *stamp* files the rows it reads (`stamp_for`), and any
    but INGESTED binds *join*'s parameters too.
    """
    if _checked(table) not in _SPECS:
        raise ValueError(f"{table!r} is not built by month")
    spec = _SPECS[table]
    if spec.raw and source is not None:
        raise ValueError(f"{table!r} reads every `updates` row, not a dedup")
    if source is None:
        where, params = month_where(month) if days is None else days_where(days)
        rows = (
            _raw_select(where, stamp)
            if spec.raw
            else _dedup_select(where, scheduled_sec=spec.scheduled_sec, stamp=stamp)
        )
    elif days is None:
        rows, params = f"SELECT * FROM {source}", {}
    else:
        rows, params = f"SELECT * FROM {source} WHERE has({{days:Array(Date)}}, date)", {"days": days}
    filed = source is None and stamp is not Stamp.INGESTED
    if join is not None and (filed or table in STATIC_JOINED_TABLES):
        params = {**params, **join.params(present, month_span(month, days))}
    return spec.select(rows), params


class Rebuild(NamedTuple):
    """One table's share of a build: its *months*, and within them only the
    *changed* dates, or every date when None."""

    months: list[int]
    changed: list[date] | None


def dedup_table(agency_id: int) -> str:
    """The agency's scratch table for one month of the dedup, shared by the tables that need the month."""
    return f"dedup__work_a{int(agency_id)}"


def _untimed(label: str) -> AbstractContextManager:
    return nullcontext()


def build(
    ch,
    agency_id: int,
    rebuilds: dict[str, Rebuild],
    step: Callable[[str], AbstractContextManager] = _untimed,
    join: ScheduleJoin | None = None,
    refit: Set[date] | None = None,
) -> None:
    """Compute each of *rebuilds* into its table's work table.

    One month at a time, so a GROUP BY holds at most a month of the agency's
    rows. The month's dedup, the costliest part of a build, runs once into the
    agency's dedup table and feeds every table that month. It covers the whole
    month when any table needs every date of it, and otherwise only the changed
    dates. Filling that table costs more than one table's own dedup, so a month
    only one table needs is built straight from `updates`, and so is a raw
    table, which reads every row rather than the dedup. A table rebuilt for
    its changed dates recomputes only those; the rest of each month is copied
    from the table itself, where the last run left it. The work and dedup tables
    are dropped first, which also clears ones a dead run left behind, and the
    dedup table again once every month is built; after a failure the caller's
    `drop_work_tables` clears them.

    A table in STATIC_JOINED_TABLES needs *join*. The rows such tables are
    rebuilt from each month (the whole month, or only its changed dates) are
    first read for their version names, which narrow the archive side of the
    join to the contents those rows join; the rows whose name maps to no
    archived timetable are counted and logged once.

    With *join*'s versions in force, each month's rows are read filed under the
    version in force on their service date (`stamp_for`, given *refit*).
    """
    joined = sorted(table for table in rebuilds if table in STATIC_JOINED_TABLES)
    if joined and join is None:
        raise ValueError(f"{joined[0]!r} joins the timetable archive; build it with a ScheduleJoin")
    unarchived: Counter[str] = Counter()
    dedup = dedup_table(agency_id)
    with step("ClickHouse build setup"):
        for table in rebuilds:
            work = work_table(table, agency_id)
            ch.command(f"DROP TABLE IF EXISTS {work} SYNC")
            ch.command(f"CREATE TABLE {work} AS {table}")
        ch.command(f"DROP TABLE IF EXISTS {dedup} SYNC")
        ch.command(
            f"CREATE TABLE {dedup} ENGINE = MergeTree ORDER BY date EMPTY AS {_dedup_select('0')}",
            parameters={"agency_id": agency_id},
        )
    for month in sorted({month for rebuild in rebuilds.values() for month in rebuild.months}):
        days = {
            table: _days_in(rebuild.changed, month) for table, rebuild in rebuilds.items() if month in rebuild.months
        }
        stems = None
        first, following = month_bounds(month)
        scoped = (
            [first + timedelta(days=i) for i in range((following - first).days)]
            if any(table_days is None for table_days in days.values())
            else sorted({day for table_days in days.values() for day in table_days or ()})
        )
        stamp = stamp_for(join, refit, scoped)
        joined_days = [table_days for table, table_days in days.items() if table in STATIC_JOINED_TABLES]
        if join is not None and joined_days:
            with step("ClickHouse version names"):
                counts = version_names(
                    ch, agency_id, *_scope_where(month, joined_days), join, stamp, month_span(month, scoped)
                )
            stems = list(counts)
            unarchived.update({stem: n for stem, n in counts.items() if stem not in join.stems})
        deduped = {table: table_days for table, table_days in days.items() if not _SPECS[table].raw}
        if len(deduped) == 1:
            ((table, table_days),) = deduped.items()
            with step(f"{table}: ClickHouse build"):
                _build_month(
                    ch, table, agency_id, month, table_days, source=None, join=join, present=stems, stamp=stamp
                )
        elif deduped:
            where, params = _scope_where(month, deduped.values())
            with step("ClickHouse dedup"):
                ch.command(
                    f"INSERT INTO {dedup} {_dedup_select(where, stamp=stamp)}",
                    parameters={
                        "agency_id": agency_id,
                        **params,
                        **_filing_params(join, stamp, between=month_span(month, scoped)),
                    },
                )
            for table, table_days in deduped.items():
                with step(f"{table}: ClickHouse build"):
                    _build_month(ch, table, agency_id, month, table_days, source=dedup, join=join, present=stems)
            with step("ClickHouse build setup"):
                ch.command(f"TRUNCATE TABLE {dedup}")
        for table, table_days in days.items():
            if _SPECS[table].raw:
                with step(f"{table}: ClickHouse build"):
                    _build_month(
                        ch, table, agency_id, month, table_days, source=None, join=join, present=stems, stamp=stamp
                    )
    with step("ClickHouse build setup"):
        ch.command(f"DROP TABLE IF EXISTS {dedup} SYNC")
    if unarchived and join is not None:
        joined_to = "joined the loaded timetable" if join.current else "had no timetable to join"
        detail = ", ".join(f"{stem or '(none)'}: {n:,}" for stem, n in unarchived.most_common(5))
        logger.info(
            f"  schedule join: {sum(unarchived.values()):,} rows of versions not archived {joined_to} ({detail})"
        )


def _days_in(changed: list[date] | None, month: int) -> list[date] | None:
    """*changed*'s dates within *month*, or None for every date of it."""
    if changed is None:
        return None
    return [day for day in changed if day.year * 100 + day.month == month] or None


def _build_month(
    ch,
    table: str,
    agency_id: int,
    month: int,
    days: list[date] | None,
    source: str | None,
    join: ScheduleJoin | None = None,
    present: list[str] | None = None,
    stamp: Stamp = Stamp.INGESTED,
) -> None:
    work = work_table(table, agency_id)
    columns = ", ".join(COLUMNS[table])
    if days is not None:
        ch.command(
            f"INSERT INTO {work} ({columns}) SELECT {columns} FROM {table} "
            "WHERE agency_id = {agency_id:Int32} AND toYYYYMM(date) = {month:UInt32} "
            "AND NOT has({days:Array(Date)}, date)",
            parameters={"agency_id": agency_id, "month": month, "days": days},
        )
    sql, params = select_sql(table, month, days, source=source, join=join, present=present, stamp=stamp)
    ch.command(f"INSERT INTO {work} ({columns}) {sql}", parameters={"agency_id": agency_id, **params})


def swap(ch, table: str, agency_id: int, months: list[int]) -> None:
    """Move each of *months* from the agency's work table into *table*.

    A month the work table holds no row of has no source rows left, so its
    partition is dropped instead: REPLACE PARTITION refuses a source partition
    with no parts.
    """
    work = work_table(table, agency_id)
    built = {row[0] for row in ch.query(f"SELECT DISTINCT toYYYYMM(date) FROM {work}").result_rows}
    if not built <= set(months):
        raise RuntimeError(f"{work} holds months {sorted(built - set(months))} outside the rebuild")
    for month in months:
        partition = f"({int(agency_id)}, {int(month)})"
        if month in built:
            ch.command(f"ALTER TABLE {table} REPLACE PARTITION {partition} FROM {work}")
        else:
            ch.command(f"ALTER TABLE {table} DROP PARTITION {partition}")


def whole_history_sql(table: str, source: str | None = None) -> str:
    """The SELECT computing *table*'s rows for the agency bound as `agency_id`, in COLUMNS order.

    *source* yields the agency's rows of the table's internal table. Without it
    those are computed in the query from the agency's whole history: what a
    read-only session runs. Computed so, a table in STATIC_JOINED_TABLES binds
    a ScheduleJoin's parameters (`ScheduleJoin.params`).
    """
    if table not in _WHOLE_SPECS:
        raise ValueError(f"{table!r} is not a whole-history aggregate")
    if source is None:
        internal = _SPECS[_WHOLE_SPECS[table].source]
        source = internal.select(
            _raw_select("1") if internal.raw else _dedup_select("1", scheduled_sec=internal.scheduled_sec)
        )
    return _WHOLE_SPECS[table].select(source)


def whole_history_source(table: str) -> str:
    """The internal table *table* is finalized from."""
    return _WHOLE_SPECS[_checked(table)].source


def _internal_source(agency_id: int, internal: str, months: list[int] | None) -> tuple[str, dict]:
    """The agency's rows of *internal* as a run that rebuilt *months* of it sees
    them: those months from its work table, every other month from *internal* itself."""
    live = f"SELECT * FROM {internal} WHERE agency_id = {{agency_id:Int32}}"
    if not months:
        return live, {}
    return (
        f"{live} AND NOT has({{months:Array(UInt32)}}, toYYYYMM(date)) "
        f"UNION ALL SELECT * FROM {work_table(internal, agency_id)}",
        {"months": months},
    )


def build_whole_history(
    ch,
    agency_id: int,
    tables: Iterable[str],
    recounted: dict[str, list[int]],
    step: Callable[[str], AbstractContextManager] = _untimed,
) -> None:
    """Compute each whole-history table in *tables* into its work table, from the
    agency's rows of its internal table. Runs after `build` and before any swap, so
    the months `build` just rebuilt of each internal table (*recounted*) are read
    from that table's work table."""
    for table in tables:
        internal = whole_history_source(table)
        source, params = _internal_source(agency_id, internal, recounted.get(internal))
        work = work_table(table, agency_id)
        columns = ", ".join(COLUMNS[table])
        with step(f"{table}: ClickHouse build"):
            ch.command(f"DROP TABLE IF EXISTS {work} SYNC")
            ch.command(f"CREATE TABLE {work} AS {table}")
            ch.command(
                f"INSERT INTO {work} ({columns}) {whole_history_sql(table, source)}",
                parameters={"agency_id": agency_id, **params},
            )


def swap_whole_history(ch, table: str, agency_id: int) -> None:
    """Move the agency's work table into *table*'s agency partition, or drop the
    partition when the work table holds no row: REPLACE PARTITION refuses a source
    partition with no parts."""
    if table not in _WHOLE_SPECS:
        raise ValueError(f"{table!r} is not a whole-history aggregate")
    work = work_table(table, agency_id)
    ((rows,),) = ch.query(f"SELECT count() FROM {work}").result_rows
    if rows:
        ch.command(f"ALTER TABLE {table} REPLACE PARTITION {int(agency_id)} FROM {work}")
    else:
        ch.command(f"ALTER TABLE {table} DROP PARTITION {int(agency_id)}")


def source_months(ch, agency_id: int) -> list[int]:
    """Every month of service date from the agency's first `updates` row to its last.

    Two probes that the (agency_id, captured_at) sort key serves from either end
    of the agency's rows, rather than a read of its whole history. Each end is
    taken a day further out, the furthest a row is filed from its capture's
    service date (`static_archive.FILED_DATE_SQL`). A month in the range that
    holds no row builds nothing, and `swap` then drops it.
    """
    ends = []
    for direction, step in (("ASC", -1), ("DESC", 1)):
        rows = ch.query(
            f"SELECT toYYYYMM(addDays({service_date_ch()}, {step})) FROM {UPDATES_TABLE} "
            f"WHERE agency_id = {{agency_id:UInt16}} ORDER BY captured_at {direction} LIMIT 1",
            parameters={"agency_id": agency_id},
        ).result_rows
        if not rows:
            return []
        ends.append(rows[0][0])
    months, month = [], ends[0]
    while month <= ends[1]:
        months.append(month)
        month = month + 1 if month % 100 < 12 else (month // 100 + 1) * 100 + 1
    return months


def months_for(ch, table: str, agency_id: int, changed: list[date] | None) -> list[int]:
    """The months a rebuild of *table* covers: those holding a *changed* date, or,
    when None, every month the agency has in `updates` or in *table*, so a month
    whose source rows are all gone is emptied too."""
    if changed is not None:
        return months_of(changed)
    stored = ch.query(
        f"SELECT DISTINCT toYYYYMM(date) FROM {_checked(table)} WHERE agency_id = {{agency_id:Int32}}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return sorted(set(source_months(ch, agency_id)) | {row[0] for row in stored})


def drop_work_tables(ch, agency_id: int) -> None:
    """Drop the agency's work tables and its dedup table. SYNC, so their disk is
    freed now rather than after the Atomic database's drop delay."""
    for name in (
        *(work_table(table, agency_id) for table in ALL_TABLES),
        dedup_table(agency_id),
    ):
        ch.command(f"DROP TABLE IF EXISTS {name} SYNC")


def _agency_rows(projection: str, table: str, agency_id: int, months: list[int] | None) -> tuple[str, dict]:
    sql = f"SELECT {projection} FROM {_checked(table)} WHERE agency_id = {{agency_id:Int32}}"
    params: dict = {"agency_id": agency_id}
    if months is not None:
        sql += " AND toYYYYMM(date) IN {months:Array(UInt32)}"
        params["months"] = months
    return sql, params


def rows_sql(table: str, agency_id: int, months: list[int] | None) -> tuple[str, dict]:
    """The SELECT reading the agency's rows of *table* in COLUMNS order, over
    *months* or, when None, all of them."""
    return _agency_rows(", ".join(COLUMNS[table]), table, agency_id, months)


def store_figures(table: str) -> _Figures:
    """The two (ClickHouse, Postgres) figures *table*'s store check compares beside the row count."""
    if table in INTERNAL_TABLES:
        raise ValueError(f"{table!r} has no Postgres copy; REFERENCES holds its check")
    return _ALL_SPECS[_checked(table)].figures


def totals(ch, table: str, agency_id: int, months: list[int] | None) -> tuple[int, int, int]:
    """(rows, and store_figures(table)'s ClickHouse figures) for the agency over
    *months*, or all of them when None: what a store check compares with *table*'s
    Postgres copy."""
    (first, _), (second, _) = store_figures(table)
    sql, params = _agency_rows(f"count(), {first}, {second}", table, agency_id, months)
    count, one, two = ch.query(sql, parameters=params).result_rows[0]
    return int(count), int(one or 0), int(two or 0)


# Per internal table, which has no Postgres copy: the Postgres table whose agency
# figure it must reproduce, and that figure as a ClickHouse and a Postgres
# expression. A difference means the internal table lost or gained rows since the
# copy was finalized from it (a dropped partition, a swap whose Postgres
# transaction rolled back), which the next run recounts before finalizing anything.
REFERENCES: dict[str, tuple[str, str, str]] = {
    # Both cover every typed, routed stop event of the history.
    COUNTS: ("agg_route_stats", "sum(samples)", "sum(samples)"),
    # Both hold every (stop, route) pair the history observed. A list's routes are
    # its commas plus one, since a route code holds no comma: string_to_array
    # would count a list of one empty code as none.
    ROUTE_STOP_DAYS: (
        "agg_stop_routes",
        "uniqExact(stop_id, route_code)",
        "sum(1 + char_length(route_codes) - char_length(replace(route_codes, ',', '')))",
    ),
}


def reference_figure(ch, internal: str, agency_id: int) -> int:
    """REFERENCES' ClickHouse figure for the agency's rows of *internal*."""
    expression = REFERENCES[internal][1]
    ((value,),) = ch.query(
        f"SELECT {expression} FROM {internal} WHERE agency_id = {{agency_id:Int32}}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return int(value or 0)


def missing_tables(ch) -> list[str]:
    """The tables in ALL_TABLES the connected database lacks."""
    rows = ch.query(
        "SELECT name FROM system.tables WHERE database = currentDatabase() AND has({names:Array(String)}, name)",
        parameters={"names": list(ALL_TABLES)},
    ).result_rows
    present = {row[0] for row in rows}
    return [table for table in ALL_TABLES if table not in present]
