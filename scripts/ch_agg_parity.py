"""Check the ClickHouse-computed aggregates against dev Postgres, and time them, writing nothing.

    poetry run python scripts/ch_agg_parity.py --agency 1
    poetry run python scripts/ch_agg_parity.py --agency 8 --time-only

Parity runs each table's ClickHouse build as a plain SELECT, one month at a time,
and compares its rows with the Postgres table's on the dates whose aggregates are
current: those whose Postgres ledger (agg_feed_health) still matches ClickHouse's
per-date counts. A date ingested or changed since the last analyze would differ
for that reason alone. The whole-history tables are computed in the query from
the agency's whole history and compared whole, and only when every date is
current, since each of their rows spans every date.

--time-only skips the comparison and times each month's build instead: the dedup
on its own, then each table's SELECT, each run in full with its rows discarded on
the server. Each table's SELECT dedups `updates` itself there, so the per-table
figures are what an unshared build would pay. A run instead fills one dedup per
month into a table the tables needing that month share (`ch_aggregates.build`),
paying the dedup column's figure plus the cost of writing it; a month only one
table needs is built straight from `updates`. A raw table reads `updates`
without the dedup either way.

Neither mode reads a ClickHouse aggregate table, so both run before the schema
that adds them is applied. The static-joined tables join ClickHouse's
static_stop_times, the schedule mirror that schema adds and only an analyze
fills, so both modes skip them until it holds the agency's schedule. Both
connections are read-only: ClickHouse with readonly=1, Postgres in a read-only
session.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from datetime import time as time_of_day

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clickhouse_connect  # noqa: E402  (sys.path injection above)
import psycopg2  # noqa: E402

from pipeline import ch_aggregates  # noqa: E402
from pipeline.analyze import _ledger_counts  # noqa: E402
from pipeline.clickhouse import ch_conn_kwargs  # noqa: E402
from pipeline.db import build_dedup_ch_sql  # noqa: E402

_TIMEOUT_SEC = 3600


def normalized(value):
    """*value* as both stores can be compared on: floats at float32 precision, instants in UTC, arrays as tuples,
    and a time of day as its HH:MM:SS text, the form ClickHouse holds it in."""
    if isinstance(value, float):
        return float(f"{value:.7g}")
    if isinstance(value, time_of_day):
        return value.isoformat()
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if isinstance(value, list):
        return tuple(value)
    return value


def comparable(table: str, row) -> tuple:
    """*row* normalized, with agg_stop_routes' route list as a sorted tuple of codes:
    ClickHouse orders the list by bytes, the Postgres copy by its collation."""
    values = tuple(map(normalized, row))
    if table == "agg_stop_routes":
        return (*values[:-1], tuple(sorted(values[-1].split(","))))
    return values


def comparable_dates(stored: dict, live: dict) -> set:
    """The dates whose ledger row matches ClickHouse's counts, so their aggregates are current."""
    return {day for day, counts in stored.items() if live.get(day) == counts}


def whole_history_current(stored: dict, live: dict) -> bool:
    """Whether every date's aggregates are current: the ledger matches ClickHouse
    date for date, with none missing from it. A whole-history row spans every
    date, so it is comparable only then."""
    return stored == live


def differences(ch_rows, pg_rows, dates) -> tuple[list, list]:
    """Rows only ClickHouse has and rows only Postgres has, on *dates*.

    Every row's second column is its date, as in ch_aggregates.COLUMNS.
    """
    ch = {tuple(map(normalized, row)) for row in ch_rows if row[1] in dates}
    pg = {tuple(map(normalized, row)) for row in pg_rows if row[1] in dates}
    return sorted(ch - pg, key=str), sorted(pg - ch, key=str)


def unmirrored(ch, agency_id: int) -> frozenset[str]:
    """The static-joined tables while ClickHouse holds no schedule for the agency, else none."""
    mirror = ch.query(
        "SELECT 1 FROM system.tables WHERE database = currentDatabase() AND name = {name:String}",
        parameters={"name": ch_aggregates.STATIC_STOP_TIMES},
    ).result_rows
    if mirror and ch_aggregates.static_mirror_fingerprint(ch, agency_id) is not None:
        return frozenset()
    print(f"{', '.join(sorted(ch_aggregates.STATIC_JOINED_TABLES))}: skipped; no schedule mirror until an analyze")
    return ch_aggregates.STATIC_JOINED_TABLES


def _pg_month(conn, table: str, agency_id: int, month: int) -> list:
    first, following = ch_aggregates.month_bounds(month)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(ch_aggregates.COLUMNS[table])} FROM {table} "
            "WHERE agency_id = %s AND date >= %s AND date < %s",
            (agency_id, first, following),
        )
        return cur.fetchall()


def parity(ch, conn, agency_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT date, raw_samples, total_rows FROM agg_feed_health WHERE agency_id = %s", (agency_id,))
        stored = {day: (raw, total) for day, raw, total in cur.fetchall()}
    live = _ledger_counts(agency_id, ch)
    dates = comparable_dates(stored, live)
    print(f"agency {agency_id}: {len(dates)} of {len(stored)} ledger dates are current")
    if not dates:
        print("parity: nothing to compare: no ledger date matches ClickHouse's counts")
        return 1
    skipped = unmirrored(ch, agency_id)
    failures = 0
    for table in ch_aggregates.TABLES:
        if table in skipped:
            continue
        compared = 0
        for month in ch_aggregates.months_of(dates):
            sql, params = ch_aggregates.select_sql(table, month)
            ch_rows = ch.query(sql, parameters={"agency_id": agency_id, **params}).result_rows
            pg_rows = _pg_month(conn, table, agency_id, month)
            only_ch, only_pg = differences(ch_rows, pg_rows, dates)
            compared += sum(1 for row in pg_rows if row[1] in dates)
            for label, rows in (("only in ClickHouse", only_ch), ("only in Postgres", only_pg)):
                for row in rows[:5]:
                    print(f"  {table} {month} {label}: {row}")
            failures += len(only_ch) + len(only_pg)
        print(f"{table}: {compared} Postgres rows compared")
    for table in ch_aggregates.WHOLE_HISTORY_TABLES:
        if table in skipped:
            continue
        if not whole_history_current(stored, live):
            print(f"{table}: skipped; a whole-history row spans every date, and not every date is current")
            continue
        ch_rows = ch.query(ch_aggregates.whole_history_sql(table), parameters={"agency_id": agency_id}).result_rows
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(ch_aggregates.COLUMNS[table])} FROM {table} WHERE agency_id = %s", (agency_id,)
            )
            pg_rows = cur.fetchall()
        ch_set = {comparable(table, row) for row in ch_rows}
        pg_set = {comparable(table, row) for row in pg_rows}
        for label, differing in (("only in ClickHouse", ch_set - pg_set), ("only in Postgres", pg_set - ch_set)):
            for row in sorted(differing, key=str)[:5]:
                print(f"  {table} {label}: {row}")
        failures += len(ch_set ^ pg_set)
        print(f"{table}: {len(pg_rows)} Postgres rows compared")
    print("parity: OK" if not failures else f"parity: {failures} differing rows")
    return 1 if failures else 0


def _timed(ch, sql: str, params: dict) -> float:
    """Seconds *sql* takes to run in full. FORMAT Null has the server produce
    every column and discard the rows, so nothing the build computes is skipped."""
    start = time.perf_counter()
    ch.raw_query(f"{sql} FORMAT Null", parameters=params)
    return time.perf_counter() - start


def timing(ch, agency_id: int) -> int:
    months = ch_aggregates.source_months(ch, agency_id)
    print(f"agency {agency_id}: {len(months)} months")
    skipped = unmirrored(ch, agency_id)
    tables = [table for table in ch_aggregates.MONTHLY_TABLES if table not in skipped]
    totals = dict.fromkeys(["dedup", *tables], 0.0)
    for month in months:
        where, params = ch_aggregates.month_where(month)
        line = {
            "dedup": _timed(
                ch, build_dedup_ch_sql(extra_where=where, include_captured_at=True), {"agency_id": agency_id, **params}
            )
        }
        for table in tables:
            sql, month_params = ch_aggregates.select_sql(table, month)
            line[table] = _timed(ch, sql, {"agency_id": agency_id, **month_params})
        for key, seconds in line.items():
            totals[key] += seconds
        print(f"  {month}: " + "  ".join(f"{key} {seconds:.1f}s" for key, seconds in line.items()), flush=True)
    print("total: " + "  ".join(f"{key} {seconds:.1f}s" for key, seconds in totals.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only parity and timing of the ClickHouse-computed aggregates.")
    parser.add_argument("--agency", type=int, required=True)
    parser.add_argument("--time-only", action="store_true")
    args = parser.parse_args(argv)
    ch = clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1}, send_receive_timeout=_TIMEOUT_SEC)
    if args.time_only:
        return timing(ch, args.agency)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        conn.set_session(readonly=True)
        return parity(ch, conn, args.agency)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
