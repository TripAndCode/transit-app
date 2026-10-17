"""Aggregates computed and stored in ClickHouse.

Each table here is built from `updates` by a ClickHouse GROUP BY into a
ClickHouse table with the name and columns of its Postgres counterpart.
`pipeline.analyze` drives the build and copies the agency's rows into the
Postgres table the API reads; this module is the ClickHouse side only.

A run replaces whole months of service date. Each table is partitioned by
(agency_id, month), a run assembles each month it touches in the agency's work
table, recomputing only the dates whose rows changed from one dedup of the month
that the tables needing it share, and `swap` then moves each month into place
with REPLACE PARTITION, so a reader never sees a month half built.

Every value equals what the Postgres builder it replaced produced, column for
column; tests/pipeline/ch_parity.py keeps that builder as the parity oracle.
Means are rounded half away from zero from exact integer sums, which is what
Postgres's ROUND(numeric) does; ClickHouse's round() on a float rounds half to
even and is never used for them.
"""

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
from datetime import date, timedelta
from typing import NamedTuple

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
from pipeline.service_day import service_date_ch, service_day_start_utc


def months_of(dates: Iterable[date]) -> list[int]:
    """The months holding *dates*, as ascending YYYYMM integers (`toYYYYMM`'s form)."""
    return sorted({d.year * 100 + d.month for d in dates})


def month_bounds(month: int) -> tuple[date, date]:
    """YYYYMM *month* as [its first day, the next month's first day)."""
    first = date(month // 100, month % 100, 1)
    return first, (first + timedelta(days=32)).replace(day=1)


def month_where(month: int) -> tuple[str, dict]:
    """A predicate selecting the `updates` rows whose service date falls in *month*.

    A range on the stored instant rather than a test of the service date, so the
    (agency_id, captured_at) sort key prunes the scan to the month. The bounds
    bind as untyped DateTime64: clickhouse-connect renders an aware datetime in
    the server's timezone, which is the zone an untyped value is read in.
    """
    first, following = month_bounds(month)
    return (
        "u.captured_at >= {month_start:DateTime64} AND u.captured_at < {month_end:DateTime64}",
        {"month_start": service_day_start_utc(first), "month_end": service_day_start_utc(following)},
    )


def days_where(days: list[date]) -> tuple[str, dict]:
    """A predicate selecting the `updates` rows whose service date is one of *days*.

    Ranges on the stored instant, for the reason `month_where` gives.
    """
    clauses, params = [], {}
    for i, day in enumerate(days):
        clauses.append(
            f"(u.captured_at >= {{day_start_{i}:DateTime64}} AND u.captured_at < {{day_end_{i}:DateTime64}})"
        )
        params[f"day_start_{i}"] = service_day_start_utc(day)
        params[f"day_end_{i}"] = service_day_start_utc(day + timedelta(days=1))
    return " OR ".join(clauses), params


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


# scheduled_time is a time of day written as hours, minutes and optionally
# seconds, colon-separated, the hours with or without a leading zero, and never
# past 23 (see pipeline/strategies/_time.py). Its field before the first colon is
# the hour Postgres's EXTRACT(HOUR FROM scheduled_time) gave.
_HOUR = "toInt16(splitByChar(':', assumeNotNull(d.scheduled_time))[1])"


def _hour_daily(source: str) -> str:
    avg_min = _round_half_away("s", "60 * n", 2)
    return f"""
        SELECT toInt32({{agency_id:UInt16}}) AS agency_id, date, hour, {avg_min} AS avg_min,
               toInt32(n) AS samples, s AS sum_delay_sec
        FROM (
            SELECT d.date AS date, {_HOUR} AS hour, sum(assumeNotNull(d.dep_delay)) AS s, count() AS n
            FROM ({source}) AS d
            WHERE d.scheduled_time IS NOT NULL
            GROUP BY d.date, {_HOUR}
        )
    """


class _Spec(NamedTuple):
    columns: tuple[str, ...]
    select: Callable[[str], str]


# Build and swap order. Each spec's columns are its Postgres table's, in the
# order the copy writes them.
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
    "agg_hour_daily": _Spec(("agency_id", "date", "hour", "avg_min", "samples", "sum_delay_sec"), _hour_daily),
}
TABLES: tuple[str, ...] = tuple(_SPECS)
COLUMNS: dict[str, tuple[str, ...]] = {name: spec.columns for name, spec in _SPECS.items()}


def _checked(table: str) -> str:
    if table not in _SPECS:
        raise ValueError(f"Not a ClickHouse aggregate: {table!r}")
    return table


# A ClickHouse regular expression matching every name work_table and dedup_table
# return; the test fixtures find every agency's scratch tables by it.
WORK_TABLE_PATTERN = "__work_a[0-9]+$"


def work_table(table: str, agency_id: int) -> str:
    """The agency's scratch copy of *table*.

    Per agency, because the agency lock serializes only that agency's runs:
    another agency's run may be building its own copy at the same time.
    """
    return f"{_checked(table)}__work_a{int(agency_id)}"


def _dedup_select(where: str) -> str:
    """The dedup of the agency's `updates` rows matching *where*, with every column a table reads."""
    return build_dedup_ch_sql(extra_where=where, include_captured_at=True)


def select_sql(table: str, month: int, days: list[date] | None = None, source: str | None = None) -> tuple[str, dict]:
    """The SELECT computing *table*'s rows for the agency bound as `agency_id`:
    those of one month, or of only *days* within it.

    Its columns are COLUMNS[table], in order. Without *source* it dedups
    `updates` itself: what a read-only session runs, and what `build` runs for a
    month only one table needs. *source* names a table already holding the
    month's dedup (`build`'s dedup table), which it reads instead.
    """
    spec = _SPECS[_checked(table)]
    if source is None:
        where, params = month_where(month) if days is None else days_where(days)
        rows = _dedup_select(where)
    elif days is None:
        rows, params = f"SELECT * FROM {source}", {}
    else:
        rows, params = f"SELECT * FROM {source} WHERE has({{days:Array(Date)}}, date)", {"days": days}
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
    ch, agency_id: int, rebuilds: dict[str, Rebuild], step: Callable[[str], AbstractContextManager] = _untimed
) -> None:
    """Compute each of *rebuilds* into its table's work table.

    One month at a time, so a GROUP BY holds at most a month of the agency's
    rows. The month's dedup, the costliest part of a build, runs once into the
    agency's dedup table and feeds every table that month. It covers the whole
    month when any table needs every date of it, and otherwise only the changed
    dates. Filling that table costs more than one table's own dedup, so a month
    only one table needs is built straight from `updates`. A table rebuilt for
    its changed dates recomputes only those; the rest of each month is copied
    from the table itself, where the last run left it. The work and dedup tables
    are dropped first, which also clears ones a dead run left behind, and the
    dedup table again once every month is built; after a failure the caller's
    `drop_work_tables` clears them.
    """
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
        if len(days) == 1:
            ((table, table_days),) = days.items()
            with step(f"{table}: ClickHouse build"):
                _build_month(ch, table, agency_id, month, table_days, source=None)
            continue
        if any(table_days is None for table_days in days.values()):
            where, params = month_where(month)
        else:
            where, params = days_where(sorted({day for table_days in days.values() for day in table_days or ()}))
        with step("ClickHouse dedup"):
            ch.command(f"INSERT INTO {dedup} {_dedup_select(where)}", parameters={"agency_id": agency_id, **params})
        for table, table_days in days.items():
            with step(f"{table}: ClickHouse build"):
                _build_month(ch, table, agency_id, month, table_days, source=dedup)
        with step("ClickHouse build setup"):
            ch.command(f"TRUNCATE TABLE {dedup}")
    with step("ClickHouse build setup"):
        ch.command(f"DROP TABLE IF EXISTS {dedup} SYNC")


def _days_in(changed: list[date] | None, month: int) -> list[date] | None:
    """*changed*'s dates within *month*, or None for every date of it."""
    if changed is None:
        return None
    return [day for day in changed if day.year * 100 + day.month == month] or None


def _build_month(ch, table: str, agency_id: int, month: int, days: list[date] | None, source: str | None) -> None:
    work = work_table(table, agency_id)
    columns = ", ".join(COLUMNS[table])
    if days is not None:
        ch.command(
            f"INSERT INTO {work} ({columns}) SELECT {columns} FROM {table} "
            "WHERE agency_id = {agency_id:Int32} AND toYYYYMM(date) = {month:UInt32} "
            "AND NOT has({days:Array(Date)}, date)",
            parameters={"agency_id": agency_id, "month": month, "days": days},
        )
    sql, params = select_sql(table, month, days, source=source)
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


def source_months(ch, agency_id: int) -> list[int]:
    """Every month of service date from the agency's first `updates` row to its last.

    Two probes that the (agency_id, captured_at) sort key serves from either end
    of the agency's rows, rather than a read of its whole history. A month in the
    range that holds no row builds nothing, and `swap` then drops it.
    """
    ends = []
    for direction in ("ASC", "DESC"):
        rows = ch.query(
            f"SELECT toYYYYMM({service_date_ch()}) FROM {UPDATES_TABLE} "
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
    for name in (*(work_table(table, agency_id) for table in TABLES), dedup_table(agency_id)):
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


def totals(ch, table: str, agency_id: int, months: list[int] | None) -> tuple[int, int, int]:
    """(rows, sum of samples, sum of sum_delay_sec) for the agency over *months*,
    or all of them when None: the figures the Postgres copy is checked against."""
    sql, params = _agency_rows("count(), sum(samples), sum(sum_delay_sec)", table, agency_id, months)
    count, samples, delay = ch.query(sql, parameters=params).result_rows[0]
    return int(count), int(samples), int(delay)


def missing_tables(ch) -> list[str]:
    """The tables in TABLES the connected database lacks."""
    rows = ch.query(
        "SELECT name FROM system.tables WHERE database = currentDatabase() AND has({names:Array(String)}, name)",
        parameters={"names": list(TABLES)},
    ).result_rows
    present = {row[0] for row in rows}
    return [table for table in TABLES if table not in present]
