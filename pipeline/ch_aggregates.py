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

from collections.abc import Iterable
from datetime import date, timedelta

from pipeline.service_day import service_day_start_utc


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
