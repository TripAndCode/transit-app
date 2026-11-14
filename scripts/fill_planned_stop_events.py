"""Fill the stop events of each saved planned day that has none.

A day saved into planned_trips_daily before the capture saved stop events
beside its trips keeps them unknown (NULL), and the feed-quality scorecard
leaves it out. For each such day this computes them as the capture would have
saved them, with the same SQL (pipeline.planned_trips.rows_plan), from the
archived timetable in force that day (pipeline.static_archive): the calendar
kept under that version's name and its content's kept schedule. A day is
filled only when that timetable also plans the planned trips the day was
saved with: a timetable loaded after its first day covers days the outgoing
one was captured for, and the first save stands. The kept schedule holds no
trip without a stop time, so a timetable with one plans fewer trips here and
its days stay unknown.

A day stays unknown, and is counted per agency, when no archived timetable is
in force on it, when that timetable's calendar is not archived (one archived
before calendars were: scripts/backfill_timetable_archive.py --calendars
keeps it from the kept zip), when its calendar does not cover the day, or when
its planned trips differ from the saved ones. Nothing known is overwritten.

The target is the environment's DATABASE_URL and CLICKHOUSE_*, as for every
pipeline command, its ClickHouse holding the calendar archive (make
ch-bootstrap); nothing loads .env, so export it first
(`set -a; . ./.env; set +a`). ClickHouse is only read. The dry run (the default) reports what it would
fill and writes nothing; --apply fills, and clears each filled agency's
feed_quality_monthly state, so its next analyze rebuilds the scorecard.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import groupby

import clickhouse_connect
import psycopg2

from pipeline import feed_quality, planned_trips, static_archive
from pipeline.clickhouse import ch_conn_kwargs

_NO_TIMETABLE = "with no archived timetable in force"
_NO_CALENDAR = "whose timetable's calendar is not archived"
_NOT_COVERED = "that the calendar of the timetable in force does not cover"
_OTHER_TRIPS = "saved with other planned trips than the timetable in force plans"


@dataclass
class Outcome:
    """What the fill does with an agency's days of unknown stop events: the
    stop events to fill, by day, and the days left, by why."""

    fills: dict[date, int] = field(default_factory=dict)
    left: dict[str, list[date]] = field(default_factory=lambda: defaultdict(list))
    uncalendared: set[str] = field(default_factory=set)


def plan_agency(conn, ch, agency_id: int, days: list[tuple[date, int]]) -> Outcome:
    """What to fill of the agency's *days*, each (service date, saved planned trips)."""
    outcome = Outcome()
    join = static_archive.schedule_join(ch, agency_id, None, by_service_date=True)
    segments = join.in_force()
    calendars = static_archive.calendar_versions(ch, agency_id)
    by_version: dict[tuple[str, str], list[tuple[date, int]]] = defaultdict(list)
    for day, trips in days:
        version = next(((stem, content) for first, last, stem, content in segments if first <= day <= last), None)
        if version is None:
            outcome.left[_NO_TIMETABLE].append(day)
        elif version not in calendars:
            outcome.left[_NO_CALENDAR].append(day)
            outcome.uncalendared.add(version[0])
        else:
            by_version[version].append((day, trips))
    for (stem, content), own in by_version.items():
        calendar = static_archive.archived_calendar(ch, agency_id, stem, content)
        assert calendar is not None
        plan = planned_trips.rows_plan(
            conn,
            agency_id,
            min(day for day, _ in own),
            max(day for day, _ in own),
            trips=static_archive.service_trips(ch, agency_id, content),
            calendar=calendar.calendar,
            calendar_dates=calendar.calendar_dates,
        )
        for day, trips in own:
            if day not in plan:
                outcome.left[_NOT_COVERED].append(day)
            elif plan[day].trips != trips:
                outcome.left[_OTHER_TRIPS].append(day)
            else:
                outcome.fills[day] = plan[day].stop_events
    conn.rollback()
    return outcome


def report(agency_id: int, days: int, outcome: Outcome) -> str:
    parts = [f"{len(outcome.fills)} to fill"]
    for why in (_NO_TIMETABLE, _NO_CALENDAR, _NOT_COVERED, _OTHER_TRIPS):
        if outcome.left.get(why):
            parts.append(f"{len(outcome.left[why])} {why}")
    line = f"agency {agency_id}: {days} day(s) without stop events: {', '.join(parts)}"
    if outcome.uncalendared:
        line += f" ({', '.join(sorted(outcome.uncalendared))})"
    return line


def fill(conn, agency_id: int, fills: dict[date, int]) -> int:
    """Write *fills* to the agency's days whose stop events are still unknown
    and commit; how many days were written."""
    days = sorted(fills)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE planned_trips_daily d SET planned_stop_events = v.stop_events "
            "FROM unnest(%s::date[], %s::bigint[]) AS v(service_date, stop_events) "
            "WHERE d.agency_id = %s AND d.service_date = v.service_date AND d.planned_stop_events IS NULL",
            (days, [fills[day] for day in days], agency_id),
        )
        written = cur.rowcount
    conn.commit()
    return written


_PENDING_SQL = (
    "SELECT agency_id, service_date, planned_trips FROM planned_trips_daily "
    "WHERE planned_stop_events IS NULL ORDER BY agency_id, service_date"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="fill the days (default: report them)")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("refused: DATABASE_URL is not set", file=sys.stderr)
        return 1
    ch = clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1})
    conn = psycopg2.connect(url)
    try:
        if not args.apply:
            conn.set_session(readonly=True)
        try:
            static_archive.require_tables(ch)
        except RuntimeError as why:
            print(f"refused: {why}", file=sys.stderr)
            return 1
        with conn.cursor() as cur:
            cur.execute(_PENDING_SQL)
            pending = cur.fetchall()
        conn.rollback()
        plans: dict[int, dict[date, int]] = {}
        for agency_id, rows in groupby(pending, key=lambda row: row[0]):
            days = [(day, trips) for _agency, day, trips in rows]
            outcome = plan_agency(conn, ch, agency_id, days)
            print(report(agency_id, len(days), outcome))
            if outcome.fills:
                plans[agency_id] = outcome.fills
        total = sum(map(len, plans.values()))
        if not total:
            print("nothing to fill")
            return 0
        if not args.apply:
            print(f"dry run: {total} day(s) would be filled; --apply fills them")
            return 0
        for agency_id, fills in plans.items():
            # Cleared before the write as well as after: a filled day is no
            # longer pending, so a run stopped after its write would leave no
            # rerun to clear the state, and one analyze ran in between keeps
            # the scorecard it built before the fill.
            static_archive.clear_states(conn, agency_id, [feed_quality.TABLE])
            written = fill(conn, agency_id, fills)
            static_archive.clear_states(conn, agency_id, [feed_quality.TABLE])
            print(f"agency {agency_id}: filled {written} day(s)")
        print(f"next: analyze agencies {', '.join(map(str, plans))}; {feed_quality.TABLE} rebuilds in full")
        return 0
    finally:
        conn.close()
        ch.close()


if __name__ == "__main__":
    sys.exit(main())
