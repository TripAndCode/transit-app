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
route headways (pipeline.static_headways) are copied there too. Last, the
agency's archive-joined tables' states are cleared, so its next analyze
rebuilds them in full against the timetable in force on each row's service
date. --every-zip also takes each kept zip whose
name no row carries: a timetable fetched but never loaded while its days were
ingested can still be the one in force on them.

--headways re-records instead the kept headways (pipeline.static_headways) of
each timetable the archive maps a name to whose headways are not kept at the
current version: one kept before the current rule, or never. analyze records
only the loaded timetable's, and a replaced one cannot be read from Postgres
again, so its kept zip is loaded into the scratch the same way, and its
headways are recorded there and copied to the target only when the zip
reproduces the mapped content. A zip whose span is not the one recorded for its
name is not loaded, since load_static would record its span again, nor is one
that cannot be read; one that fails to load is reported, and the next tried,
as is each agency left after one that fails. This mode
writes nothing to ClickHouse: it reads it through a read-only session, never
archives or maps, and leaves the archive, the map and its mirror as they were.
Each agency whose headways it writes has the states of the tables built from
the kept headways cleared (pipeline.static_headways.kept_headway_readers), so
its next analyze rebuilds those, judging each day again by its timetable's
headways, and leaves its other tables standing. Without --agency it takes every
agency the map names, reporting and skipping one the target does not hold,
which --agency naming it refuses. A mapped content with no kept zip that loads
and reproduces it keeps what it had, and is reported. --apply then exits 1, and
goes on exiting 1 on every rerun until each such content is resolved, by its zip
restored or otherwise.

The target is the environment's DATABASE_URL and CLICKHOUSE_*, as for every
pipeline command. The scratch is --scratch-database-url: a throwaway Postgres,
already migrated (make migrate DATABASE_URL=<scratch>), that holds no other
agency under this id. The dry run (the default) lists the candidates and writes
nothing. Nothing is ever deleted from the archive or the map.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
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
from pipeline.static_loader import load_static, zip_span
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


def _check_identity(target_conn, scratch_conn) -> None:
    if _identity(target_conn) == _identity(scratch_conn):
        raise Refusal("the scratch database is the target database")


def _check_scratch(target_conn, scratch_conn, agency_id: int) -> tuple:
    """The target's agency row, once the scratch is shown to be another database that holds no other agency
    under this id."""
    _check_identity(target_conn, scratch_conn)
    row = _agency_row(target_conn, scratch_conn, agency_id)
    if row is None:
        raise Refusal(f"agency {agency_id} is not in the target database")
    return row


def _agency_row(target_conn, scratch_conn, agency_id: int) -> tuple | None:
    """The target's agency row, None when the target has no such agency, once the scratch is shown to
    hold no other agency under this id."""
    with target_conn.cursor() as cur:
        cur.execute(
            "SELECT agency_name, feed_url, static_url, timezone FROM agencies WHERE agency_id = %s", (agency_id,)
        )
        row = cur.fetchone()
    target_conn.rollback()
    if row is None:
        return None
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


# Every column route_headway_by_content keeps of a route, beside its key.
_KEPT_COLUMNS = (
    "route_code, logic_version, scheduled_headway_median_sec, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2"
)


def copy_medians(scratch_conn, target_conn, agency_id: int, key: str) -> int:
    """Copy content *key*'s kept headways, medians and sums, from the scratch to the target; the rows inserted."""
    with scratch_conn.cursor() as cur:
        cur.execute(
            f"SELECT {_KEPT_COLUMNS} FROM {static_headways.TABLE} "
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
            f"INSERT INTO {static_headways.TABLE} (agency_id, content_hash, {_KEPT_COLUMNS}) "
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


def _load_scratch(scratch_conn, ch, agency_id: int, path: Path) -> list[str]:
    """Load the zip at *path* into the scratch, in place of the agency's timetable there; its content parts."""
    _empty_scratch_timetable(scratch_conn, agency_id)
    load_static(str(path), agency_id, scratch_conn, ch_client=ch)
    return static_parts(agency_id, scratch_conn, True)


def _backfill(agency_id: int, target_conn, ch, scratch_conn, chosen: list[Candidate]) -> list[tuple[str, str]]:
    """Archive and map each of *chosen*; then clear the states, also when a later step fails, so the dates
    of every name mapped rebuild."""
    done: list[tuple[str, str]] = []
    mapping = False
    try:
        for candidate in chosen:
            assert candidate.zip is not None
            parts = _load_scratch(scratch_conn, ch, agency_id, candidate.zip)
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


@dataclass(frozen=True)
class Archived:
    """One archived timetable of the agency: its content, the version names mapped to it, and their kept zips."""

    content: str
    stems: tuple[str, ...]
    zips: tuple[Path, ...]


def headway_candidates(
    mapped: dict[str, str], current: set[str], static_dir: Path, agency_id: int
) -> tuple[list[Archived], list[Archived]]:
    """The contents *mapped* (each version name to its content) names, oldest name first: those *current*
    holds, whose headways are kept at the current version, and the others."""
    stems: dict[str, list[str]] = defaultdict(list)
    for stem, content in sorted(mapped.items()):
        stems[content].append(stem)
    kept: list[Archived] = []
    other: list[Archived] = []
    for content, names in sorted(stems.items(), key=lambda item: (item[1][0], item[0])):
        zips = tuple(path for path in (static_dir / str(agency_id) / f"{stem}.zip" for stem in names) if path.is_file())
        (kept if content in current else other).append(Archived(content, tuple(names), zips))
    return kept, other


def _archived_contents(ch, agency_id: int) -> set[str]:
    rows = ch.query(
        f"SELECT DISTINCT content_hash FROM {static_archive.ARCHIVE} WHERE agency_id = {{agency:Int32}}",
        parameters={"agency": agency_id},
    ).result_rows
    return {row[0] for row in rows}


# The columns a timetable's kept sums are written to (migration 0098).
_SUM_COLUMNS = ("scheduled_headway_sum_sec", "scheduled_headway_sumsq_sec2")


def _has_sums(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = ANY(%s)",
            (static_headways.TABLE, list(_SUM_COLUMNS)),
        )
        found = cur.fetchone()[0]
    conn.rollback()
    return found == len(_SUM_COLUMNS)


def _names(archived: Archived) -> str:
    return ", ".join(archived.stems)


def _unloadable(path: Path, recorded: tuple[date, date] | None) -> str | None:
    """Why the kept zip at *path* is not loaded to re-record its timetable, or None when it is: load_static
    records the zip's span for its name, so a zip whose span is not the *recorded* one would move the days
    its name is in force."""
    try:
        span = zip_span(str(path))
    except Exception as why:
        return f"could not read it: {why}"
    return None if span == recorded else f"its span is not the one recorded for {path.stem}"


def _plan_headways(target_conn, ch, agency_id: int, static_dir: Path) -> list[Archived]:
    """List the agency's archived timetables by whether their headways are current, to be re-recorded, or
    without a kept zip that can be loaded; those not current, each with only its zips that can be."""
    join = static_archive.schedule_join(ch, agency_id, None)
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    current = static_headways.current_contents(target_conn, agency_id)
    target_conn.rollback()
    kept, other = headway_candidates(mapped, current, static_dir, agency_id)
    recorded = static_archive.spans(ch, agency_id)
    unloadable = {
        path: why for archived in other for path in archived.zips if (why := _unloadable(path, recorded.get(path.stem)))
    }
    loadable = [
        replace(archived, zips=tuple(path for path in archived.zips if path not in unloadable)) for archived in other
    ]
    chosen = sum(1 for archived in loadable if archived.zips)
    without = sum(1 for archived in other if not archived.zips)
    unmapped = sorted(_archived_contents(ch, agency_id) - set(mapped.values()))
    print(
        f"agency {agency_id}: {len(kept) + len(other)} archived timetable(s): {chosen} to re-record, "
        f"{len(kept)} current, {without} without a kept zip, {len(other) - chosen - without} whose kept zips "
        "cannot be loaded"
    )
    for archived in kept:
        print(f"  current {archived.content}: {_names(archived)}")
    for archived, fit in zip(other, loadable, strict=True):
        for path in archived.zips:
            if path in unloadable:
                print(f"  {path}: {unloadable[path]}; not loaded")
        if fit.zips:
            print(f"  re-record {archived.content}: {_names(archived)}, from {', '.join(map(str, fit.zips))}")
        elif archived.zips:
            print(f"  no loadable zip {archived.content}: {_names(archived)}; not re-recorded")
        else:
            print(f"  no kept zip {archived.content}: {_names(archived)}; not re-recorded")
    for content in unmapped:
        print(f"  not mapped {content}: no version name carries it, so no row reads its headways; not re-recorded")
    return loadable


def _rollback(conn) -> None:
    # A connection that failed can refuse even this; the failure is already reported.
    with contextlib.suppress(psycopg2.Error):
        conn.rollback()


def _record_in_scratch(scratch_conn, ch, agency_id: int, path: Path, content: str) -> str | None:
    """Load the zip at *path* into the scratch and record its headways there as *content*'s; why they
    were not, or None once they are."""
    parts = _load_scratch(scratch_conn, ch, agency_id, path)
    if static_archive.content_hash(parts) != content:
        return f"does not reproduce {content}"
    static_headways.record_route_headways(scratch_conn, agency_id, content, parts)
    scratch_conn.commit()
    recorded = content in static_headways.current_contents(scratch_conn, agency_id)
    scratch_conn.rollback()
    return None if recorded else "its timetable gives no route headways"


def _rerecord(agency_id: int, target_conn, ch, scratch_conn, chosen: list[Archived], done: list[str]) -> None:
    """Record each of *chosen*'s headways from the first of its zips that reproduces its content, copy them
    to the target, and add the content to *done*. A zip that cannot be loaded is reported, and the next one
    tried. The states of the tables built from the kept headways are cleared before the first copy, so a
    run stopped after it still rebuilds them, and again at the end, past any analyze that ran in between."""
    readers = static_headways.kept_headway_readers()
    cleared = False
    try:
        for archived in chosen:
            for path in archived.zips:
                try:
                    failed = _record_in_scratch(scratch_conn, ch, agency_id, path, archived.content)
                except Exception as why:
                    _rollback(scratch_conn)
                    print(f"  could not load {path}: {why}")
                    continue
                if failed:
                    print(f"  {path}: {failed}")
                    continue
                if not cleared:
                    static_archive.clear_states(target_conn, agency_id, readers)
                    cleared = True
                copied = copy_medians(scratch_conn, target_conn, agency_id, archived.content)
                done.append(archived.content)
                print(f"re-recorded {archived.content} from {path.stem}: {copied} route(s)")
                break
            if archived.content not in done:
                print(f"  {archived.content}: not re-recorded; it keeps the headways it had")
    finally:
        if cleared:
            # A statement that failed leaves the target's transaction aborted, which would refuse the
            # clear and put its own error in place of the failure's.
            _rollback(target_conn)
            static_archive.clear_states(target_conn, agency_id, readers)


def _headways(args, ch, target_conn, scratch_conn) -> int:
    """List, or with --apply re-record, the kept headways of each agency's archived timetables; 1 when
    --apply leaves any of them not current."""
    _check_identity(target_conn, scratch_conn)
    if args.agency is not None:
        agencies = [args.agency]
    else:
        agencies = sorted({mapping.agency_id for mapping in static_archive.latest_mappings(ch)})
    rows: dict[int, tuple] = {}
    plans: dict[int, list[Archived]] = {}
    for agency in agencies:
        row = _agency_row(target_conn, scratch_conn, agency)
        if row is None:
            if args.agency is not None:
                raise Refusal(f"agency {agency} is not in the target database")
            print(f"agency {agency}: not in the target database; skipped")
            continue
        rows[agency] = row
        plans[agency] = _plan_headways(target_conn, ch, agency, args.static_dir)
    chosen = {agency: [archived for archived in plan if archived.zips] for agency, plan in plans.items()}
    total = sum(map(len, chosen.values()))
    without_sums = [
        which for which, conn in (("target", target_conn), ("scratch", scratch_conn)) if not _has_sums(conn)
    ]
    if not total:
        print("nothing to re-record")
    elif not args.apply:
        for which in without_sums:
            print(f"note: the {which} database keeps no scheduled-gap sums; --apply refuses until `make migrate`")
        print(
            f"dry run: {total} timetable(s) would be re-recorded; --apply loads each kept zip and re-records the "
            "timetable it reproduces"
        )
    if not args.apply:
        return 0
    if total and without_sums:
        raise Refusal(f"the {' and '.join(without_sums)} database keeps no scheduled-gap sums; run `make migrate`")
    if total:
        try:
            static_archive.require_mirror(scratch_conn)
        except RuntimeError as why:
            raise Refusal(f"the scratch database: {why}") from why
        scratch_conn.rollback()
    done: dict[int, list[str]] = {}
    try:
        for agency, archived in chosen.items():
            if not archived:
                continue
            done[agency] = []
            try:
                _ensure_agency(scratch_conn, agency, rows[agency])
                _rerecord(agency, target_conn, ch, scratch_conn, archived, done[agency])
            except Exception as why:
                _rollback(target_conn)
                _rollback(scratch_conn)
                print(f"agency {agency}: stopped, {why}", file=sys.stderr)
    finally:
        written = [agency for agency, contents in done.items() if contents]
        if written:
            print(
                f"next: analyze agencies {', '.join(map(str, written))}; "
                f"{', '.join(static_headways.kept_headway_readers())} rebuild in full"
            )
    left = sum(map(len, plans.values())) - sum(map(len, done.values()))
    if left:
        print(f"{left} timetable(s) not re-recorded; each keeps the headways it had", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agency", type=int, metavar="ID", help="the agency (with --headways: every one mapped)")
    parser.add_argument("--scratch-database-url", required=True, help="a throwaway, migrated Postgres to load into")
    parser.add_argument(
        "--static-dir", type=Path, default=Path(DEFAULT_STATIC_DEST), help="where the static fetcher keeps the zips"
    )
    parser.add_argument(
        "--apply", action="store_true", help="archive and map the candidates, or re-record them (default: list them)"
    )
    parser.add_argument("--every-zip", action="store_true", help="also take kept zips whose name no row carries")
    parser.add_argument(
        "--headways", action="store_true", help="re-record the kept headways of archived timetables instead"
    )
    args = parser.parse_args(argv)
    if args.headways and args.every_zip:
        parser.error("--every-zip does not apply to --headways")
    if not args.headways and args.agency is None:
        parser.error("--agency is required without --headways")
    url = os.environ.get("DATABASE_URL")
    try:
        if not url:
            raise Refusal("DATABASE_URL is not set")
        # The dry run only reads, and its sessions say so; --headways never
        # writes to ClickHouse, so its session says so under --apply too.
        if args.apply and not args.headways:
            ch = get_client()
        else:
            ch = clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1})
        target_conn = psycopg2.connect(url)
        scratch_conn = psycopg2.connect(args.scratch_database_url)
        if not args.apply:
            target_conn.set_session(readonly=True)
            scratch_conn.set_session(readonly=True)
        try:
            if args.headways:
                return _headways(args, ch, target_conn, scratch_conn)
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
