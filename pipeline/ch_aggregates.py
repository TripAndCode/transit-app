"""Aggregates computed and stored in ClickHouse.

Each table here is built from `updates` by a ClickHouse GROUP BY into a
ClickHouse table with the name and columns of its Postgres counterpart.
`pipeline.analyze` drives the build and copies the agency's rows into the
Postgres table the API reads; this module is the ClickHouse side only.

A run rebuilds whole months of service date. Each table is partitioned by
(agency_id, month), a run computes its months into the agency's work table, and
`swap` then moves each month into place with REPLACE PARTITION, so a reader
never sees a month half built.

Every value equals what the Postgres builder produced, column for column.
Means are rounded half away from zero from exact integer sums, which is what
Postgres's ROUND(numeric) does; ClickHouse's round() on a float rounds half to
even and is never used for them.
"""

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import NamedTuple

from pipeline.clickhouse import UPDATES_TABLE
from pipeline.db import build_dedup_ch_sql
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


# scheduled_time is "HH:MM" or "HH:MM:SS" and never names an hour past 23 (see
# pipeline/strategies/_time.py), so its leading field is the hour Postgres's
# EXTRACT(HOUR FROM scheduled_time) gave.
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
    with_captured_at: bool = False


# Build and swap order. Each spec's columns are its Postgres table's, in the
# order the copy writes them.
_SPECS: dict[str, _Spec] = {
    "agg_hour_daily": _Spec(("agency_id", "date", "hour", "avg_min", "samples", "sum_delay_sec"), _hour_daily),
}
TABLES: tuple[str, ...] = tuple(_SPECS)
COLUMNS: dict[str, tuple[str, ...]] = {name: spec.columns for name, spec in _SPECS.items()}


def _checked(table: str) -> str:
    if table not in _SPECS:
        raise ValueError(f"Not a ClickHouse aggregate: {table!r}")
    return table


def work_table(table: str, agency_id: int) -> str:
    """The agency's scratch copy of *table*.

    Per agency, because the agency lock serializes only that agency's runs:
    another agency's run may be building its own copy at the same time.
    """
    return f"{_checked(table)}__work_a{int(agency_id)}"


def select_sql(table: str, month: int) -> tuple[str, dict]:
    """The SELECT computing one month of *table* for the agency bound as `agency_id`.

    Its columns are COLUMNS[table], in order. A plain SELECT, so a read-only
    session can run it too (scripts/ch_agg_parity.py).
    """
    spec = _SPECS[_checked(table)]
    where, params = month_where(month)
    source = build_dedup_ch_sql(extra_where=where, include_captured_at=spec.with_captured_at)
    return spec.select(source), params


def build(ch, table: str, agency_id: int, months: list[int]) -> None:
    """Compute *months* of *table* for the agency into its work table.

    One INSERT per month, so the dedup's GROUP BY holds one month of the
    agency's rows at a time rather than its whole history. The work table is
    dropped first, which also clears one a dead run left behind.
    """
    work = work_table(table, agency_id)
    ch.command(f"DROP TABLE IF EXISTS {work} SYNC")
    ch.command(f"CREATE TABLE {work} AS {table}")
    columns = ", ".join(COLUMNS[table])
    for month in months:
        sql, params = select_sql(table, month)
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
    """Every month of service date holding a row of the agency's `updates`."""
    rows = ch.query(
        f"SELECT DISTINCT toYYYYMM({service_date_ch()}) FROM {UPDATES_TABLE} WHERE agency_id = {{agency_id:UInt16}}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return sorted(row[0] for row in rows)


def months_for(ch, table: str, agency_id: int, scope: list[int] | None) -> list[int]:
    """The months a rebuild of *table* covers: *scope*, or, when None, every month
    the agency has in `updates` or in *table*, so a month whose source rows are
    all gone is emptied too."""
    if scope is not None:
        return scope
    stored = ch.query(
        f"SELECT DISTINCT toYYYYMM(date) FROM {_checked(table)} WHERE agency_id = {{agency_id:Int32}}",
        parameters={"agency_id": agency_id},
    ).result_rows
    return sorted(set(source_months(ch, agency_id)) | {row[0] for row in stored})


def drop_work_tables(ch, agency_id: int) -> None:
    """Drop the agency's work tables. SYNC, so their disk is freed now rather
    than after the Atomic database's drop delay."""
    for table in TABLES:
        ch.command(f"DROP TABLE IF EXISTS {work_table(table, agency_id)} SYNC")


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
