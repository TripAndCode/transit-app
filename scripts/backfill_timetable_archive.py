"""Backfill the timetable archive from the kept static zips.

A row stamped with a version name the archive does not map joins whatever
timetable is loaded (pipeline.static_archive.ScheduleJoin), and a mapped name
with no recorded span, or whose content's schedule is not kept, is in force on
no date (pipeline.timetable_in_force), so its rows keep the timetable they were
ingested against. For each such name of the agency whose zip the static
fetcher kept (<static-dir>/<agency>/<name>.zip), --apply loads the zip into the
scratch Postgres with the real load_static, whose archive writes go to the
target ClickHouse: it records the name's span from the zip.
static_archive.archive_loaded then keeps its stop times and schedule there and
maps the name to them, mirroring the mapping in the target Postgres, and its
route medians (pipeline.static_headways) are copied there too. Last, the agency's archive-joined tables' states
are cleared, so its next analyze rebuilds them in full against the timetable
in force on each row's service date. --every-zip also takes each kept zip whose
name no row carries: a timetable fetched but never loaded while its days were
ingested can still be the one in force on them.

The target is the environment's DATABASE_URL and CLICKHOUSE_*, as for every
pipeline command. The scratch is --scratch-database-url: a throwaway Postgres,
already migrated (make migrate DATABASE_URL=<scratch>), that holds no other
agency under this id. The dry run (the default) lists the candidates and writes
nothing. Nothing is ever deleted from the archive or the map.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import clickhouse_connect
import psycopg2
import psycopg2.extras

from pipeline import static_archive, static_headways
from pipeline.clickhouse import UPDATES_TABLE, ch_conn_kwargs, get_client
from pipeline.service_day import service_date_ch
from pipeline.static_content import STATIC_DEPENDENCY_COLUMNS, static_parts
from pipeline.static_fetcher import DEFAULT_STATIC_DEST
from pipeline.static_loader import load_static
from pipeline.timetable_in_force import fetch_day


class Refusal(Exception):
    """A precondition the run refuses to go on without."""


@dataclass(frozen=True)
class Candidate:
    stem: str
    rows: int
    first: date
    last: date
    zip: Path | None


def candidates(
    stems: dict[str, tuple[int, date, date]], mapped: set[str], static_dir: Path, agency_id: int
) -> list[Candidate]:
    """The names in *stems* (name to its rows, first and last service date) that are not NULL and not
    *mapped*, oldest first, each with its kept zip, or None without one."""
    found = []
    for stem, (rows, first, last) in stems.items():
        if not stem or stem in mapped:
            continue
        path = static_dir / str(agency_id) / f"{stem}.zip"
        found.append(Candidate(stem, rows, first, last, path if path.is_file() else None))
    return sorted(found, key=lambda candidate: (candidate.first, candidate.stem))


def complete_names(ch, agency_id: int) -> set[str]:
    """The mapped names a row can be filed under (pipeline.timetable_in_force):
    each has a recorded span, and its content's schedule is kept."""
    join = static_archive.schedule_join(ch, agency_id, None)
    spans = static_archive.spans(ch, agency_id)
    kept = static_archive.scheduled_contents(ch, agency_id)
    return {stem for stem, content in zip(join.stems, join.hashes, strict=True) if stem in spans and content in kept}


def with_kept_zips(
    stems: dict[str, tuple[int, date, date]], static_dir: Path, agency_id: int
) -> dict[str, tuple[int, date, date]]:
    """*stems*, plus each zip kept for the agency that no row's name is, with no rows, at its fetch day."""
    found = dict(stems)
    for path in sorted((static_dir / str(agency_id)).glob("*.zip")):
        day = fetch_day(path.stem) or date.min
        found.setdefault(path.stem, (0, day, day))
    return found


def _stems_in_updates(ch, agency_id: int) -> dict[str, tuple[int, date, date]]:
    day = service_date_ch()
    rows = ch.query(
        f"SELECT ifNull(static_version_id, '') AS stem, count(), min({day}), max({day}) FROM {UPDATES_TABLE} "
        "WHERE agency_id = {agency:UInt16} GROUP BY stem",
        parameters={"agency": agency_id},
    ).result_rows
    return {stem: (n, first, last) for stem, n, first, last in rows}


def _identity(conn) -> tuple:
    """Which database *conn* is: its server's start time names the server."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_postmaster_start_time(), current_database()")
        identity = cur.fetchone()
    conn.rollback()
    return identity


def _check_scratch(target_conn, scratch_conn, agency_id: int) -> tuple:
    """The target's agency row, once the scratch is shown to be another database that holds no other agency
    under this id."""
    if _identity(target_conn) == _identity(scratch_conn):
        raise Refusal("the scratch database is the target database")
    with target_conn.cursor() as cur:
        cur.execute(
            "SELECT agency_name, feed_url, static_url, timezone FROM agencies WHERE agency_id = %s", (agency_id,)
        )
        row = cur.fetchone()
    target_conn.rollback()
    if row is None:
        raise Refusal(f"agency {agency_id} is not in the target database")
    with scratch_conn.cursor() as cur:
        cur.execute("SELECT agency_name FROM agencies WHERE agency_id = %s", (agency_id,))
        held = cur.fetchone()
    scratch_conn.rollback()
    if held is not None and held[0] != row[0]:
        raise Refusal(f"the scratch database holds agency {agency_id} as {held[0]!r}, not {row[0]!r}")
    return row


def _ensure_agency(scratch_conn, agency_id: int, row: tuple) -> None:
    with scratch_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_id, agency_name, feed_url, static_url, timezone) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (agency_id) DO NOTHING",
            (agency_id, *row),
        )
    scratch_conn.commit()


def copy_medians(scratch_conn, target_conn, agency_id: int, key: str) -> int:
    """Copy content *key*'s kept medians from the scratch to the target; the rows inserted."""
    with scratch_conn.cursor() as cur:
        cur.execute(
            f"SELECT route_code, logic_version, scheduled_headway_median_sec FROM {static_headways.TABLE} "
            "WHERE agency_id = %s AND content_hash = %s ORDER BY route_code, logic_version",
            (agency_id, key),
        )
        rows = cur.fetchall()
    scratch_conn.rollback()
    if not rows:
        return 0
    with target_conn.cursor() as cur:
        inserted = psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {static_headways.TABLE} "
            "(agency_id, content_hash, route_code, logic_version, scheduled_headway_median_sec) "
            "VALUES %s ON CONFLICT DO NOTHING RETURNING 1",
            [(agency_id, key, *row) for row in rows],
            fetch=True,
        )
    target_conn.commit()
    return len(inserted)


def _empty_scratch_timetable(scratch_conn, agency_id: int) -> None:
    """Remove the scratch's loaded timetable for the agency. load_static archives the timetable it
    replaces into the target, so a timetable left by an earlier, failed run would be mapped there without
    its medians."""
    with scratch_conn.cursor() as cur:
        for table in STATIC_DEPENDENCY_COLUMNS:
            cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
    scratch_conn.commit()


def _backfill(agency_id: int, target_conn, ch, scratch_conn, chosen: list[Candidate]) -> list[tuple[str, str]]:
    """Archive and map each of *chosen*; then clear the states, also when a later step fails, so the dates
    of every name mapped rebuild."""
    done: list[tuple[str, str]] = []
    mapping = False
    try:
        for candidate in chosen:
            assert candidate.zip is not None
            _empty_scratch_timetable(scratch_conn, agency_id)
            load_static(str(candidate.zip), agency_id, scratch_conn, ch_client=ch)
            parts = static_parts(agency_id, scratch_conn, True)
            if not static_archive.stop_time_count(parts):
                raise Refusal(f"{candidate.zip} loaded no stop times")
            key = static_archive.content_hash(parts)
            # The medians first: archive_loaded's map row takes the name out of
            # every later run's candidates, so it lands last.
            static_headways.record_route_headways(scratch_conn, agency_id, key, parts)
            scratch_conn.commit()
            copy_medians(scratch_conn, target_conn, agency_id, key)
            mapping = True
            if static_archive.archive_loaded(scratch_conn, ch, agency_id, parts, mirror_conn=target_conn) != key:
                raise Refusal(f"{candidate.stem} was not archived")
            done.append((candidate.stem, key))
            print(f"archived {candidate.stem} as {key}")
    finally:
        # From the first map write on, even one whose call then failed; clearing again is harmless.
        if mapping:
            static_archive.clear_archive_joined_states(target_conn, agency_id)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agency", type=int, required=True, metavar="ID")
    parser.add_argument("--scratch-database-url", required=True, help="a throwaway, migrated Postgres to load into")
    parser.add_argument(
        "--static-dir", type=Path, default=Path(DEFAULT_STATIC_DEST), help="where the static fetcher keeps the zips"
    )
    parser.add_argument("--apply", action="store_true", help="archive and map the candidates (default: list them)")
    parser.add_argument("--every-zip", action="store_true", help="also take kept zips whose name no row carries")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise Refusal("DATABASE_URL is not set")
        # The dry run only reads, and its sessions say so.
        ch = get_client() if args.apply else clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1})
        target_conn = psycopg2.connect(url)
        scratch_conn = psycopg2.connect(args.scratch_database_url)
        if not args.apply:
            target_conn.set_session(readonly=True)
            scratch_conn.set_session(readonly=True)
        try:
            row = _check_scratch(target_conn, scratch_conn, args.agency)
            stems = _stems_in_updates(ch, args.agency)
            if args.every_zip:
                stems = with_kept_zips(stems, args.static_dir, args.agency)
            found = candidates(stems, complete_names(ch, args.agency), args.static_dir, args.agency)
            for candidate in found:
                where = candidate.zip if candidate.zip is not None else "no zip, skipped"
                rows = "no rows"
                if candidate.rows:
                    rows = f"{candidate.rows:,} rows, {candidate.first} .. {candidate.last}"
                print(f"{candidate.stem}: {rows}, {where}")
            chosen = [candidate for candidate in found if candidate.zip is not None]
            if not chosen:
                print("nothing to archive")
                return 0
            if not args.apply:
                print(f"dry run: {len(chosen)} name(s) would be archived; --apply archives them")
                return 0
            # Both, before anything is mapped: the target takes each mapping's
            # mirror row, and load_static refuses a scratch without the mirror.
            for which, conn in (("target", target_conn), ("scratch", scratch_conn)):
                try:
                    static_archive.require_mirror(conn)
                except RuntimeError as why:
                    raise Refusal(f"the {which} database: {why}") from why
                conn.rollback()
            _ensure_agency(scratch_conn, args.agency, row)
            _backfill(args.agency, target_conn, ch, scratch_conn, chosen)
            print(f"next: analyze agency {args.agency}; its archive-joined tables rebuild in full")
            return 0
        finally:
            target_conn.close()
            scratch_conn.close()
    except Refusal as why:
        print(f"refused: {why}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
