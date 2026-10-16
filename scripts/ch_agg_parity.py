"""Check the ClickHouse-computed aggregates against dev Postgres, and time them, writing nothing.

    poetry run python scripts/ch_agg_parity.py --agency 1
    poetry run python scripts/ch_agg_parity.py --agency 8 --time-only

Parity runs each table's ClickHouse build as a plain SELECT, one month at a time,
and compares its rows with the Postgres table's on the dates whose aggregates are
current: those whose Postgres ledger (agg_feed_health) still matches ClickHouse's
per-date counts. A date ingested or changed since the last analyze would differ
for that reason alone.

--time-only skips the comparison and times each month's build instead: the dedup
on its own, then each table's SELECT, each wrapped in a count so the rows stay on
the server.

Neither mode reads a ClickHouse aggregate table, so both run before the schema
that adds them is applied. Both connections are read-only: ClickHouse with
readonly=1, Postgres in a read-only session.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

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
    """*value* as both stores can be compared on: floats at float32 precision, instants in UTC."""
    if isinstance(value, float):
        return float(f"{value:.7g}")
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return value


def comparable_dates(stored: dict, live: dict) -> set:
    """The dates whose ledger row matches ClickHouse's counts, so their aggregates are current."""
    return {day for day, counts in stored.items() if live.get(day) == counts}


def differences(ch_rows, pg_rows, dates) -> tuple[list, list]:
    """Rows only ClickHouse has and rows only Postgres has, on *dates*.

    Every row's second column is its date, as in ch_aggregates.COLUMNS.
    """
    ch = {tuple(map(normalized, row)) for row in ch_rows if row[1] in dates}
    pg = {tuple(map(normalized, row)) for row in pg_rows if row[1] in dates}
    return sorted(ch - pg, key=str), sorted(pg - ch, key=str)


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
    dates = comparable_dates(stored, _ledger_counts(agency_id, ch))
    print(f"agency {agency_id}: {len(dates)} of {len(stored)} ledger dates are current")
    failures = 0
    for table in ch_aggregates.TABLES:
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
    print("parity: OK" if not failures else f"parity: {failures} differing rows")
    return 1 if failures else 0


def _timed(ch, sql: str, params: dict) -> float:
    start = time.perf_counter()
    ch.query(f"SELECT count() FROM ({sql})", parameters=params)
    return time.perf_counter() - start


def timing(ch, agency_id: int) -> int:
    months = ch_aggregates.source_months(ch, agency_id)
    print(f"agency {agency_id}: {len(months)} months")
    totals = dict.fromkeys(["dedup", *ch_aggregates.TABLES], 0.0)
    for month in months:
        where, params = ch_aggregates.month_where(month)
        line = {"dedup": _timed(ch, build_dedup_ch_sql(extra_where=where), {"agency_id": agency_id, **params})}
        for table in ch_aggregates.TABLES:
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
