"""Write the refiled rows of every date an agency's history holds that has no
write a read can take (pipeline.refiled), so its reads stop joining the
timetable archive.

analyze writes the refiled dates it rebuilds, so a refiled date outside its
rebuild scope has no write a read can take: every date refiled before
analyze wrote any, every date a rewrite of `updates` wrote off
(refiled.rows_rewritten) until the full rebuild that rewrite calls for, and
every date written under other filing rules (refiled.RULES) once the rules
change. Run this again after any of those. This finds, per agency whose ingest
stamps versions and that has versions in force, the dates whose rows REFIT
refiles over its whole history (static_archive.refit_dates), and lists those
without a write that still serves a read. With --apply it writes them under
the agency's lock, as analyze would. It changes no aggregate.

The target is the environment's DATABASE_URL and CLICKHOUSE_*. The dry run
only reads.

    poetry run python scripts/write_refiled_history.py [--agency ID] [--apply]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import NamedTuple

import clickhouse_connect
import psycopg2

from pipeline import refiled, static_archive
from pipeline.clickhouse import ANALYZE_SEND_RECEIVE_TIMEOUT_SEC, ch_conn_kwargs, get_client
from pipeline.locks import agency_ingest_lock
from pipeline.strategies.static_join import VERSION_STAMPING_STRATEGIES


class Plan(NamedTuple):
    agency_id: int
    refiled: list[date]  # the dates of its history whose rows REFIT refiles
    unwritten: list[date]  # those of them no write serves a read for


def agencies(conn, agency_id: int | None) -> list[int]:
    """The agencies whose ingest stamps versions, of *agency_id* alone when given."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id FROM agencies WHERE ingest_strategy = ANY(%s) AND (%s::int IS NULL OR agency_id = %s) "
            "ORDER BY agency_id",
            (sorted(VERSION_STAMPING_STRATEGIES), agency_id, agency_id),
        )
        return [row[0] for row in cur.fetchall()]


def plan(ch, agency_id: int) -> tuple[Plan, static_archive.ScheduleJoin]:
    """What the agency's history holds refiled and unwritten, and the join it is filed under."""
    join = static_archive.schedule_join(ch, agency_id, None, by_service_date=True)
    if not join.force:
        return Plan(agency_id, [], []), join
    dates = sorted(static_archive.refit_dates(ch, agency_id, "1", {}, join))
    kept = refiled.reusable(ch, agency_id, join, dates)
    return Plan(agency_id, dates, [day for day in dates if day not in kept]), join


def run(conn, ch, agency_ids: list[int], *, apply: bool) -> list[Plan]:
    """Plan each agency of *agency_ids*, and with *apply* write its unwritten
    dates, planned again under the agency's lock so nothing analyze writes
    meanwhile is written twice."""
    plans = []
    for agency_id in agency_ids:
        if not apply:
            plans.append(plan(ch, agency_id)[0])
            continue
        with agency_ingest_lock(conn, agency_id):
            planned, join = plan(ch, agency_id)
            if planned.unwritten:
                refiled.write(ch, agency_id, join, planned.unwritten)
        plans.append(planned)
    return plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agency", type=int, metavar="ID", help="only this agency (default: every one)")
    parser.add_argument("--apply", action="store_true", help="write the unwritten dates (default: list them)")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    # A scan of an agency's whole history outlasts the default receive timeout.
    if args.apply:
        ch = get_client(send_receive_timeout=ANALYZE_SEND_RECEIVE_TIMEOUT_SEC)
    else:
        ch = clickhouse_connect.get_client(
            **ch_conn_kwargs(), settings={"readonly": 1}, send_receive_timeout=ANALYZE_SEND_RECEIVE_TIMEOUT_SEC
        )
    conn = psycopg2.connect(url)
    try:
        if not args.apply:
            conn.set_session(readonly=True)
        plans = run(conn, ch, agencies(conn, args.agency), apply=args.apply)
    finally:
        conn.close()
    for p in plans:
        span = f", {p.unwritten[0]} .. {p.unwritten[-1]}" if p.unwritten else ""
        print(f"agency {p.agency_id}: {len(p.refiled)} refiled date(s), {len(p.unwritten)} unwritten{span}")
    unwritten = sum(len(p.unwritten) for p in plans)
    if not args.apply:
        print(f"dry run: {unwritten} date(s) would be written; --apply writes them")
    else:
        print(f"wrote {unwritten} date(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
