"""Every timetable an agency loads, kept in ClickHouse under its content.

analyze joins a day's rows to the timetable in force on their service date
(pipeline.timetable_in_force), or to the one they were ingested against where
none is known to be, and either may no longer be loaded. So each timetable is
kept here before it is replaced (load_static) and again by analyze while it is
loaded (a backstop for one loaded before this existed): its stop times, and
its schedule as ingest stamps a poll matching each stop time. Content, not the
version name, is the key: a reload under a new name with the same content
stores nothing new, and the version map says which content each name carried.
The days each name covers are kept beside the map, as its span. Postgres
mirrors the map, each name at its latest content (MIRROR), for the definition
stamp, which reads it on every request.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from enum import Enum
from typing import NamedTuple

import psycopg2.extras

from pipeline.clickhouse import UPDATE_COLUMNS, UPDATES_TABLE, checked_table
from pipeline.service_day import service_date_ch
from pipeline.static_content import static_parts
from pipeline.timetable_in_force import Segment, Version, gtfs_date, in_force, schedule_row, version_span

logger = logging.getLogger(__name__)

ARCHIVE = "static_stop_times_archive"
SCHEDULE = "static_schedule_archive"
VERSION_MAP = "static_version_map"
SPANS = "static_version_spans"
# The Postgres mirror of VERSION_MAP (migration 0097).
MIRROR = "static_version_contents"
_BATCH = 50_000


# A row's join content: the archived timetable its version name maps to, else
# the loaded one. Bound parameters only (ScheduleJoin.params), so the SQL text
# never depends on data.
JOIN_CONTENT_SQL = (
    "transform(static_version_id, {join_stems:Array(String)}, {join_hashes:Array(String)}, {join_current:String})"
)


def archived_stop_times_sql(columns: str) -> str:
    """The archived stop times of the contents a join binds (join_contents), as a subquery."""
    return (
        f"(SELECT {columns} FROM {ARCHIVE} "
        "WHERE agency_id = {agency_id:UInt16} AND has({join_contents:Array(String)}, content_hash))"
    )


# One archived stop-time row, NULL told apart from '': the dwell join reads both times.
_STOP_TIME_ROW_HASH = (
    "cityHash64(stop_sequence, stop_id, arrival_time IS NULL, ifNull(arrival_time, ''), "
    "departure_time IS NULL, ifNull(departure_time, ''))"
)


def changed_trips_sql() -> str:
    """The trip_ids whose archived stop times differ between the agency's contents
    {before} and {after}, or that only one of them has, as a subquery.

    Per trip and content, an order-independent sum of row hashes plus a row
    count, as pipeline.static_content keys a whole timetable. *before* and
    *after* must differ: one content alone reads as every trip changing.
    """
    return (
        f"(SELECT trip_id FROM (SELECT trip_id, content_hash, sum({_STOP_TIME_ROW_HASH}) AS h, count() AS n "
        f"FROM {ARCHIVE} WHERE agency_id = {{agency_id:UInt16}} "
        "AND content_hash IN ({before:String}, {after:String}) GROUP BY trip_id, content_hash) "
        "GROUP BY trip_id HAVING count() = 1 OR uniqExact(h, n) = 2)"
    )


def content_hash(parts: list[str]) -> str:
    """The archive key for a timetable whose content parts are *parts*."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def require_tables(ch) -> None:
    """Refuse to run against a ClickHouse store that predates the archive."""
    names = {
        row[0] for row in ch.query("SELECT name FROM system.tables WHERE database = currentDatabase()").result_rows
    }
    missing = [table for table in (ARCHIVE, SCHEDULE, VERSION_MAP, SPANS) if table not in names]
    if missing:
        raise RuntimeError(f"ClickHouse has no {', '.join(missing)}; run `make ch-bootstrap`")


def require_mirror(conn) -> None:
    """Refuse to map names against a Postgres that predates the map's mirror:
    a map row written without its mirror row would leave the stamp without the
    name's content until the mirror is filled by hand."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (MIRROR,))
        missing = cur.fetchone()[0] is None
    if missing:
        raise RuntimeError(f"Postgres has no {MIRROR}; run `make migrate`")


def loaded_stem(conn, agency_id: int) -> str | None:
    """The version name of the agency's loaded timetable, or None for one loaded before names were kept."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(static_version_id) FROM static_trips WHERE agency_id = %s", (agency_id,))
        row = cur.fetchone()
    return row[0] if row else None


class _Kept(NamedTuple):
    """What the archive keeps of a content in *table*: the Postgres SELECT of
    the loaded timetable's rows, the agency bound as %s, and each row's
    columns after agency_id and content_hash, as *row* shapes them."""

    table: str
    columns: list[str]
    select: str
    row: Callable[[tuple], tuple]


_STOP_TIMES = _Kept(
    ARCHIVE,
    ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"],
    "SELECT trip_id, stop_sequence, stop_id, arrival_time, departure_time FROM static_stop_times WHERE agency_id = %s",
    lambda row: row,
)
# A stop time whose trip static_trips lacks matches no poll at ingest, so it has no schedule row.
_SCHEDULE = _Kept(
    SCHEDULE,
    ["trip_id", "stop_sequence", "service_type", "route_id", "scheduled_time", "scheduled_sec"],
    "SELECT st.trip_id, st.stop_sequence, t.service_id, t.route_id, st.departure_time FROM static_stop_times st "
    "JOIN static_trips t ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id WHERE st.agency_id = %s",
    lambda row: schedule_row(*row),
)


def _work_table(table: str, agency_id: int, key: str) -> str:
    # Unique per attempt: a load's outgoing archive and an analyze backstop can
    # archive the same content at once, and must not drop each other's table.
    # It ends like an aggregate work table, so the test fixtures' sweep by
    # ch_aggregates.WORK_TABLE_PATTERN finds one a killed copy left behind.
    # Nothing reuses the name, so in a store such a table stays until dropped
    # by hand.
    return f"{table}_{key[:8]}_{secrets.token_hex(4)}__work_a{int(agency_id)}"


def stop_time_count(parts: list[str]) -> int:
    """How many stop times the timetable whose content parts are *parts* holds."""
    for part in parts:
        table, count, _ = part.split(":")
        if table == "static_stop_times":
            return int(count)
    return 0


def _holds(ch, table: str, agency_id: int, key: str) -> bool:
    rows = ch.query(
        f"SELECT count() FROM {table} WHERE agency_id = {{agency:Int32}} AND content_hash = {{key:String}}",
        parameters={"agency": agency_id, "key": key},
    ).result_rows
    return bool(rows and rows[0][0])


def is_archived(ch, agency_id: int, key: str) -> bool:
    """Whether the archive holds stop times of the agency's content *key*."""
    return _holds(ch, ARCHIVE, agency_id, key)


def archive_loaded(conn, ch, agency_id: int, parts: list[str] | None = None, mirror_conn=None) -> str | None:
    """Archive the agency's loaded timetable unless its content is already kept,
    map its version name to that content, mirror the name's mapping into
    Postgres, record the name's span unless one is recorded, and return the
    content's key.

    None when no stop times are loaded, or when the timetable changed while it
    was being copied (a concurrent load_static committed): nothing is swapped
    in then, and the next call archives what is loaded. A returned key does
    not promise the mapping: a name read after the timetable changed is left
    unmapped, for the next call to record. *parts*, when the
    caller already read them, saves reading them again; they must be the
    loaded timetable's. *mirror_conn* is the Postgres the map is mirrored in
    (MIRROR), *conn* unless the timetable is loaded in another database than
    the one the app reads (scripts/backfill_timetable_archive.py's scratch).
    """
    if parts is None:
        parts = static_parts(agency_id, conn, True)
    expected = stop_time_count(parts)
    if expected == 0:
        return None
    key = content_hash(parts)
    for kept, count in ((_STOP_TIMES, expected), (_SCHEDULE, None)):
        if not _holds(ch, kept.table, agency_id, key) and not _copy(conn, ch, agency_id, key, parts, kept, count):
            return None
    _map(conn, ch, agency_id, key, parts, conn if mirror_conn is None else mirror_conn)
    _record_loaded_span(conn, ch, agency_id)
    return key


def _copy(conn, ch, agency_id: int, key: str, parts: list[str], kept: _Kept, expected: int | None) -> bool:
    """Copy the loaded timetable's rows of *kept* into its table under *key*,
    swapped in whole, unless the timetable no longer matches *parts* once
    copied, or the rows copied are not the *expected* count when one is given."""
    work = _work_table(kept.table, agency_id, key)
    ch.command(f"CREATE TABLE {work} AS {kept.table}")
    columns = ["agency_id", "content_hash", *kept.columns]
    try:
        copied = 0
        with conn.cursor(name=f"archive_{int(agency_id)}_{secrets.token_hex(4)}") as cur:
            cur.itersize = _BATCH
            cur.execute(kept.select, (agency_id,))
            batch: list[tuple] = []
            for row in cur:
                batch.append((agency_id, key, *kept.row(row)))
                if len(batch) == _BATCH:
                    ch.insert(work, batch, column_names=columns)
                    copied += len(batch)
                    batch = []
            if batch:
                ch.insert(work, batch, column_names=columns)
                copied += len(batch)
        # The key was read before the copy, in another statement: a load that
        # committed in between would leave another timetable's rows under it.
        if (expected is not None and copied != expected) or static_parts(agency_id, conn, True) != parts:
            logger.warning(f"  timetable archive: agency {agency_id}'s timetable changed while copying; not archived")
            return False
        # A content none of whose stop times has its trip loaded has no
        # schedule: no poll matched it at ingest either, so there is none to keep.
        if copied:
            ch.command(f"ALTER TABLE {kept.table} REPLACE PARTITION ({int(agency_id)}, '{key}') FROM {work}")
        logger.info(f"  timetable archive: agency {agency_id} content {key} kept in {kept.table} ({copied:,} rows)")
        return True
    finally:
        ch.command(f"DROP TABLE IF EXISTS {work} SYNC")


_SCHEDULED_SQL = f"SELECT DISTINCT content_hash FROM {SCHEDULE} WHERE agency_id = {{agency:Int32}}"
_SPANS_SQL = (
    f"SELECT static_version_id, argMax(tuple(first_day, last_day), recorded_at) FROM {SPANS} "
    "WHERE agency_id = {agency:Int32} GROUP BY static_version_id"
)
_VERSION_MAP_SQL = (
    f"SELECT static_version_id, argMax(content_hash, archived_at) FROM {VERSION_MAP} "
    "WHERE agency_id = {agency:Int32} GROUP BY static_version_id ORDER BY static_version_id"
)


def scheduled_contents(ch, agency_id: int) -> set[str]:
    """The agency's contents whose schedule the archive keeps."""
    return {row[0] for row in ch.query(_SCHEDULED_SQL, parameters={"agency": agency_id}).result_rows}


def _spans(rows) -> dict[str, tuple[date, date]]:
    return {stem: (span[0], span[1]) for stem, span in rows}


def spans(ch, agency_id: int) -> dict[str, tuple[date, date]]:
    """Each of the agency's version names with a recorded span, at its latest one."""
    return _spans(ch.query(_SPANS_SQL, parameters={"agency": agency_id}).result_rows)


def record_span(ch, agency_id: int, stem: str, span: tuple[date, date]) -> None:
    """Record that version *stem* covers *span*, unless that is already its latest span.

    A changed span moves which version is in force on the days between the
    two, which analyze finds by comparing what it last built against
    (pipeline.analyze), so nothing is cleared here.
    """
    if spans(ch, agency_id).get(stem) == span:
        return
    ch.insert(
        SPANS,
        [(agency_id, stem, span[0], span[1], datetime.now(timezone.utc))],
        column_names=["agency_id", "static_version_id", "first_day", "last_day", "recorded_at"],
    )


# The loaded name and its calendar's bounds, read in one statement so they
# describe one timetable. calendar_dates holds raw text, compared here only as
# eight digits, which sort in calendar order.
_LOADED_CALENDAR_SQL = """
    SELECT (SELECT max(static_version_id) FROM static_trips WHERE agency_id = %(agency)s),
           (SELECT min(start_date) FROM static_calendar WHERE agency_id = %(agency)s),
           (SELECT max(end_date) FROM static_calendar WHERE agency_id = %(agency)s),
           (SELECT min(date) FROM static_calendar_dates WHERE agency_id = %(agency)s AND date ~ '^[0-9]{8}$'),
           (SELECT max(date) FROM static_calendar_dates WHERE agency_id = %(agency)s AND date ~ '^[0-9]{8}$')
"""


def _record_loaded_span(conn, ch, agency_id: int) -> None:
    """Record the loaded version's span from its calendar when it has none.

    load_static records each timetable's span as it loads it, from
    feed_info.txt where the zip has one, so this reaches only a timetable
    loaded before spans were kept, whose feed_info.txt is gone.
    """
    with conn.cursor() as cur:
        cur.execute(_LOADED_CALENDAR_SQL, {"agency": agency_id})
        stem, first, last, first_date, last_date = cur.fetchone()
    if stem is None or stem in spans(ch, agency_id):
        return
    covered = [(first, last)] if first is not None and last is not None else []
    covered += [(day, day) for day in map(gtfs_date, (first_date, last_date)) if day is not None]
    span = version_span(None, None, covered)
    if span is not None:
        record_span(ch, agency_id, stem, span)


def archive_joined_tables() -> list[str]:
    """The tables that read the archive: by each row's version, or by the version in force on its service date."""
    # agg_registry imports ch_aggregates, which imports this module: imported here, at call time.
    from pipeline import agg_registry
    from pipeline.agg_registry import Timetable

    return [
        name for name, table in agg_registry.TABLES.items() if table.timetable is Timetable.ARCHIVED or table.in_force
    ]


def clear_archive_joined_states(conn, agency_id: int) -> None:
    """Clear the states of the agency's tables that read the archive, so its next analyze rebuilds them in full."""
    clear_states(conn, agency_id, archive_joined_tables())


def clear_states(conn, agency_id: int, names: list[str]) -> None:
    """Clear the states of the agency's tables *names*, so its next analyze rebuilds them in full."""
    from pipeline.locks import agency_ingest_lock

    # Under the agency lock: an analyze in flight would otherwise record these
    # states again at its end, as built, and the rebuild would be lost. Within
    # analyze itself the lock is the session's own, taken again.
    with agency_ingest_lock(conn, agency_id):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agg_table_state WHERE agency_id = %s AND table_name = ANY(%s)", (agency_id, names))
        conn.commit()


def _map(conn, ch, agency_id: int, key: str, parts: list[str], mirror_conn) -> None:
    """Record that the loaded version name carries *key*, unless that is
    already its latest mapping, and mirror the name's latest mapping into
    *mirror_conn*.

    A name already mapped to other content (a timetable refetched under the
    same name) leaves the dates ingested under it built against the other one,
    so the archive-joined tables' states are cleared and they rebuild in full.
    """
    stem = loaded_stem(conn, agency_id)
    if stem is None:
        return
    latest = ch.query(
        f"SELECT argMax(tuple(content_hash, archived_at), archived_at) FROM {VERSION_MAP} "
        "WHERE agency_id = {agency:Int32} AND static_version_id = {stem:String}",
        parameters={"agency": agency_id, "stem": stem},
    ).result_rows
    # An aggregate over no rows is still one row, ('', the epoch), and '' is no content.
    previous, previous_at = latest[0][0]
    if previous == key:
        # Mirrored again from the map's own row: a name mapped before the
        # mirror existed, or whose mirror write failed, catches up here.
        mirror(mirror_conn, [Mapping(agency_id, stem, key, previous_at)])
        return
    # A load replaces the name and the content in one commit, so content read
    # after the name still matching *parts* means the name read was *key*'s.
    if static_parts(agency_id, conn, True) != parts:
        logger.warning(f"  timetable archive: agency {agency_id}'s timetable changed before its name was mapped")
        return
    if previous:
        logger.warning(
            f"  timetable archive: agency {agency_id}'s {stem} now carries {key}, not {previous}; "
            "the archive-joined tables rebuild in full"
        )
        # Cleared and committed before the map row lands, so a process that
        # dies in between still rebuilds them; clearing again is harmless.
        clear_archive_joined_states(conn, agency_id)
    archived_at = _map_time()
    ch.insert(
        VERSION_MAP,
        [(agency_id, stem, key, archived_at)],
        column_names=["agency_id", "static_version_id", "content_hash", "archived_at"],
    )
    # After the map row, so the mirror never names a content the map does not.
    mirror(mirror_conn, [Mapping(agency_id, stem, key, archived_at)])


def _map_time() -> datetime:
    """Now, at the map's millisecond precision, so the mirror holds the map row's own time."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond - now.microsecond % 1000)


class Mapping(NamedTuple):
    """A version name's content in the map, and when the map recorded it."""

    agency_id: int
    stem: str
    content: str
    archived_at: datetime


def _utc(at: datetime) -> datetime:
    # The map's times are UTC, which a client may return without a zone.
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at


_LATEST_MAPPINGS_SQL = (
    f"SELECT agency_id, static_version_id, argMax(tuple(content_hash, archived_at), archived_at) "
    f"FROM {VERSION_MAP} GROUP BY agency_id, static_version_id ORDER BY agency_id, static_version_id"
)


def latest_mappings(ch) -> list[Mapping]:
    """Every agency's mapped version names, each at its latest content, by agency then name."""
    rows = ch.query(_LATEST_MAPPINGS_SQL).result_rows
    return [Mapping(agency, stem, latest[0], _utc(latest[1])) for agency, stem, latest in rows]


def mirror(conn, mappings: Iterable[tuple[int, str, str, datetime]]) -> int:
    """Copy *mappings* (:class:`Mapping` or its tuple), each (agency, name) at
    most once, into the map's Postgres mirror and commit; how many rows changed.

    A name keeps its row unless *mappings* maps it later, so the mirror holds
    each name at the latest content the map gives it, whichever copy lands
    last, and copying a mapping again changes nothing. Committed at once: the
    map has no rollback, and a mirror row left in a caller's transaction would
    be lost with it.
    """
    rows = [(agency_id, stem, content, _utc(archived_at)) for agency_id, stem, content, archived_at in mappings]
    if not rows:
        return 0
    with conn.cursor() as cur:
        written = psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {MIRROR} (agency_id, static_version_id, content_hash, archived_at) VALUES %s "
            "ON CONFLICT (agency_id, static_version_id) DO UPDATE "
            "SET content_hash = EXCLUDED.content_hash, archived_at = EXCLUDED.archived_at "
            f"WHERE {MIRROR}.archived_at < EXCLUDED.archived_at RETURNING 1",
            rows,
            fetch=True,
        )
    conn.commit()
    return len(written)


class ScheduleJoin(NamedTuple):
    """Which archived timetable each version name joins, and the loaded one every other row joins.

    *current* is the loaded timetable's key, or "" with none loaded: then only
    rows of a mapped name join anything. *force* is the versions in force over
    the agency's service dates (pipeline.timetable_in_force), each a mapped
    name whose content's schedule is kept, so a row of a date one covers is
    filed under it (Stamp).
    """

    stems: list[str]
    hashes: list[str]
    current: str
    force: tuple[Segment, ...] = ()

    def content_of(self) -> Callable[[str], str]:
        """The content each version name joins: its mapped one, else the loaded one."""
        mapped = dict(zip(self.stems, self.hashes, strict=True))
        return lambda stem: mapped.get(stem, self.current)

    def in_force(self) -> list[tuple[date, date, str, str]]:
        """Each segment of *force* with its content: (first, last, version name, content)."""
        mapped = dict(zip(self.stems, self.hashes, strict=True))
        return [(s.first, s.last, s.stem, mapped[s.stem]) for s in self.force]

    def filing_params(self, between: tuple[date, date]) -> dict:
        """The bound parameters observation_rows reads to file rows of the
        service dates *between* (force_params) under Stamp.IN_FORCE or REFIT."""
        return {"join_stems": self.stems, "join_hashes": self.hashes, **self.force_params(between)}

    def params(self, present: Iterable[str] | None = None, between: tuple[date, date] | None = None) -> dict:
        """The join's bound parameters. *present*, the version names of the rows
        being joined, narrows the archive side to the contents they join, and
        *between* the versions in force to those of the read's dates
        (force_params)."""
        mapped = dict(zip(self.stems, self.hashes, strict=True))
        if present is None:
            contents = {*self.hashes, self.current}
        else:
            contents = {mapped.get(stem, self.current) for stem in present}
        return {
            "join_stems": self.stems,
            "join_hashes": self.hashes,
            "join_current": self.current,
            "join_contents": sorted(contents - {""}),
            **self.force_params(between),
        }

    def force_params(self, between: tuple[date, date] | None = None) -> dict:
        """The bound parameters of the segments in force, which IN_FORCE_STEM_SQL reads.

        *between*, the first and last service dates a read can reach (both
        inclusive), keeps only the segments overlapping them, so a REFIT read
        loads only those versions' schedules. A read must not reach a date
        outside it: a row there would be read as having none in force.
        """
        mapped = dict(zip(self.stems, self.hashes, strict=True))
        force = [s for s in self.force if between is None or (s.first <= between[1] and between[0] <= s.last)]
        return {
            "force_firsts": [s.first for s in force],
            "force_lasts": [s.last for s in force],
            "force_stems": [s.stem for s in force],
            "force_contents": [mapped[s.stem] for s in force],
        }


def schedule_join(ch, agency_id: int, current: str | None, *, by_service_date: bool = False) -> ScheduleJoin:
    """The agency's version map, each name at its latest content, with *current* the loaded timetable's key.

    *by_service_date* adds the versions in force, for an agency whose ingest
    stamps each row's version (pipeline.strategies.static_join): only those
    rows' schedule came from a join a later one can redo. A version whose
    content's schedule is not kept leaves the dates it is in force over with
    none, so their rows keep what ingest joined.
    """
    parameters = {"agency": agency_id}
    rows = ch.query(_VERSION_MAP_SQL, parameters=parameters).result_rows
    if not by_service_date:
        return _schedule_join(rows, current)
    kept = ch.query(_SCHEDULED_SQL, parameters=parameters).result_rows
    return _schedule_join(rows, current, (kept, ch.query(_SPANS_SQL, parameters=parameters).result_rows))


async def schedule_join_async(
    ch, agency_id: int, current: str | None, *, by_service_date: bool = False
) -> ScheduleJoin:
    """schedule_join through an async ClickHouse client."""
    parameters = {"agency": agency_id}
    rows = (await ch.query(_VERSION_MAP_SQL, parameters=parameters)).result_rows
    if not by_service_date:
        return _schedule_join(rows, current)
    kept = (await ch.query(_SCHEDULED_SQL, parameters=parameters)).result_rows
    return _schedule_join(rows, current, (kept, (await ch.query(_SPANS_SQL, parameters=parameters)).result_rows))


def _schedule_join(map_rows, current: str | None, in_force_rows: tuple | None = None) -> ScheduleJoin:
    """The join of _VERSION_MAP_SQL's rows, with the versions in force from
    _SCHEDULED_SQL's and _SPANS_SQL's rows when *in_force_rows* holds them."""
    stems, hashes = [row[0] for row in map_rows], [row[1] for row in map_rows]
    force: tuple[Segment, ...] = ()
    if in_force_rows is not None:
        kept_rows, span_rows = in_force_rows
        mapped = dict(zip(stems, hashes, strict=True))
        kept = {row[0] for row in kept_rows}
        versions = [Version(stem, *span) for stem, span in _spans(span_rows).items() if stem in mapped]
        force = tuple(s for s in in_force(versions) if mapped[s.stem] in kept)
    return ScheduleJoin(stems, hashes, current or "", force)


class Stamp(str, Enum):
    """Which timetable a read of `updates` files each row under (observation_rows).

    INGESTED: the one ingest joined, as it stamped the row.

    IN_FORCE: the version in force on the row's service date
    (ScheduleJoin.force) names the row, and its schedule columns stay as
    ingested. Exact where that version's content is the one ingest joined,
    which is every row but those REFIT reads.

    REFIT: IN_FORCE, and a row whose ingest joined other content than the
    version in force carries that version's schedule: its service, scheduled
    time and seconds. A row whose stop that version lacks carries none, as
    ingest leaves a row the timetable misses. The content ingest joined is the
    one its version name maps to. A row with no name (its trip missed the
    timetable then loaded) or a name the archive does not map joined content
    that cannot be told, so it is read from the version in force whenever one
    is; a trip no timetable has stays without a schedule.

    The route is the one exception: a refiled row takes the version's route
    only where it has none. Ingest writes the feed's route_id, or the loaded
    timetable's route for the trip where the feed names none, and the stored
    row does not say which, so a route ingest filled from another timetable
    stays. A trip_id rarely changes route between revisions.
    """

    INGESTED = "ingested"
    IN_FORCE = "in_force"
    REFIT = "refit"


# The row's place in the bound segments in force (ScheduleJoin.params), 0 for
# a service date none covers; element 0 of an array is its type's default, ''.
_FORCE_AT = (
    "arrayFirstIndex((a, b) -> "
    + service_date_ch("u.captured_at")
    + " BETWEEN a AND b, {force_firsts:Array(Date32)}, {force_lasts:Array(Date32)})"
)

# The version name a row is filed under: the one in force on its service date, else its own stamp ('' for none).
IN_FORCE_STEM_SQL = f"if({_FORCE_AT} = 0, ifNull(u.static_version_id, ''), {{force_stems:Array(String)}}[{_FORCE_AT}])"

# The content ingest joined a row to: its version name's mapped one, or '' where it cannot be told.
_STAMPED_CONTENT = (
    "transform(ifNull(u.static_version_id, ''), {join_stems:Array(String)}, {join_hashes:Array(String)}, '')"
)

_REFIT_COLUMNS = {
    "service_type": "if(u.refit, s.service_type, u.service_type)",
    "scheduled_time": "if(u.refit, s.scheduled_time, u.scheduled_time)",
    "scheduled_sec": "if(u.refit, s.scheduled_sec, u.scheduled_sec)",
    "route_code": "if(u.refit AND u.route_code IS NULL, s.route_id, u.route_code)",
}


def _marked_rows(where: str, table: str, source: str | None = None) -> str:
    """*table*'s rows matching *where*, or *source*'s rows, each with its place
    among the segments in force, and whether REFIT reads its schedule from the
    archive."""
    rows = f"{checked_table(table)} AS u WHERE {where}" if source is None else f"{source} AS u"
    return (
        f"SELECT u.*, {_FORCE_AT} AS force_at, {{force_contents:Array(String)}}[force_at] AS force_content, "
        f"force_content != '' AND force_content != {_STAMPED_CONTENT} AS refit, "
        "if(refit, force_content, '') AS refit_content "
        f"FROM {rows}"
    )


# The columns a row is filed by (_marked_rows, _REFIT_COLUMNS), and the stop event it is part of.
_STOP_EVENT_KEYS = (
    "agency_id",
    "trip_id",
    "stop_sequence",
    "static_version_id",
    "route_code",
    "service_type",
    "scheduled_time",
    "scheduled_sec",
)


def _stop_events(where: str, table: str) -> str:
    """*table*'s rows matching *where*, one per stop event (a trip's stop on
    one service day) and stored schedule and version name, with `updates`'
    columns: the latest poll's capture, file name, departure delay and both
    schedule relationships, by (captured_at, file_name), and any poll's of the
    rest. Every poll grouped together is filed alike, so a read keeping only
    each stop event's latest poll among those it files together, and whether
    that poll withdraws the departure (pipeline.db.latest_departure_ch), reads
    the same rows from these, filing a fraction as many. The latest poll's
    fields are taken in a tuple, which argMax does not skip as it skips a
    NULL argument."""
    latest = "max((u.captured_at, u.file_name))"
    reading = (
        "argMax(tuple(u.dep_delay, u.schedule_relationship_trip, u.schedule_relationship_stop), "
        "(u.captured_at, u.file_name))"
    )
    picked = {
        "captured_at": f"tupleElement({latest}, 1)",
        "file_name": f"tupleElement({latest}, 2)",
        "dep_delay": f"tupleElement({reading}, 1)",
        "schedule_relationship_trip": f"tupleElement({reading}, 2)",
        "schedule_relationship_stop": f"tupleElement({reading}, 3)",
    }
    columns = ", ".join(
        f"u.{column} AS event_{column}"
        if column in _STOP_EVENT_KEYS
        else f"{picked.get(column, f'any(u.{column})')} AS event_{column}"
        for column in UPDATE_COLUMNS
    )
    keys = ", ".join(f"u.{column}" for column in _STOP_EVENT_KEYS)
    return (
        f"(SELECT {', '.join(f'event_{column} AS {column}' for column in UPDATE_COLUMNS)} FROM "
        f"(SELECT {columns} FROM {checked_table(table)} AS u WHERE {where} "
        f"GROUP BY {keys}, {service_date_ch('u.captured_at')}))"
    )


def _filed_columns(refit: bool) -> str:
    columns = []
    for column in UPDATE_COLUMNS:
        if column == "static_version_id":
            expression = "if(u.force_at = 0, u.static_version_id, {force_stems:Array(String)}[u.force_at])"
        elif refit and column in _REFIT_COLUMNS:
            expression = _REFIT_COLUMNS[column]
        else:
            expression = f"u.{column}"
        columns.append(f"{expression} AS {column}")
    return ", ".join(columns)


def observation_rows(
    where: str,
    stamp: Stamp,
    table: str = UPDATES_TABLE,
    filed_where: str = "",
    *,
    live_form: bool = False,
    by_stop_event: bool = False,
) -> str:
    """The FROM clause of a read of *table*'s rows matching *where*, aliased
    `u`, with `updates`' columns, each row filed under *stamp*'s timetable.

    *where* names columns as `u.` columns and is tested on each row as ingest
    stored it, so it must not test a column filing rewrites (static_version_id,
    and under REFIT service_type, scheduled_time, scheduled_sec and
    route_code): a row ingest left without a schedule would be dropped before
    REFIT gives it one. A test of those goes in *filed_where*, on the filed
    row. Under INGESTED the clause is the table itself; otherwise a subquery
    that binds ScheduleJoin.params, which files a row only by the segments in
    force. REFIT also joins the schedule archive, so a read whose rows need
    none of it (refit_dates) is cheaper under IN_FORCE, with the same result.

    *live_form*, for a read inside the API's time budget
    (pipeline.live_filing), joins the archive on one 64-bit hash of its three
    keys instead of the keys themselves, which ClickHouse probes several times
    faster. A hash two stop times share would file a row under the other's
    schedule; those reads accept that bound, while analyze, whose figures are
    versioned by this SQL's text (pipeline.agg_registry), keeps the exact
    join. *by_stop_event* files one row per stop event and stored schedule
    (_stop_events) under REFIT, for a read that keeps only each stop event's
    latest delay.
    """
    if stamp is Stamp.INGESTED:
        return f"FROM {checked_table(table)} AS u WHERE {where}" + (f" AND ({filed_where})" if filed_where else "")
    events = by_stop_event and stamp is Stamp.REFIT
    marked = _marked_rows(where, table, _stop_events(where, table) if events else None)
    after = f" WHERE {filed_where}" if filed_where else ""
    if stamp is Stamp.IN_FORCE:
        return f"FROM (SELECT {_filed_columns(False)} FROM ({marked}) AS u) AS u{after}"
    if live_form:
        keys = "cityHash64(content_hash, trip_id, stop_sequence) AS archive_key"
        on = "s.archive_key = cityHash64(u.refit_content, toString(u.trip_id), toInt32(u.stop_sequence))"
    else:
        keys = "content_hash, trip_id, stop_sequence"
        on = "s.content_hash = u.refit_content AND s.trip_id = u.trip_id AND s.stop_sequence = toInt32(u.stop_sequence)"
    return (
        f"FROM (SELECT {_filed_columns(True)} FROM ({marked}) AS u "
        f"ANY LEFT JOIN (SELECT {keys}, service_type, route_id, scheduled_time, "
        f"scheduled_sec FROM {SCHEDULE} WHERE agency_id = {{agency_id:UInt16}} "
        "AND has({force_contents:Array(String)}, content_hash)) AS s "
        f"ON {on}) AS u{after}"
    )


def refit_dates(
    ch, agency_id: int, where: str, params: dict, join: ScheduleJoin, between: tuple[date, date] | None = None
) -> set[date]:
    """The service dates holding a row matching *where* (on `u.` columns) whose
    schedule Stamp.REFIT reads from the archive. *between* bounds the dates
    *where* can reach (ScheduleJoin.force_params)."""
    sql = (
        f"SELECT DISTINCT {service_date_ch('captured_at')} FROM "
        f"({_marked_rows(f'u.agency_id = {{agency_id:UInt16}} AND ({where})', UPDATES_TABLE)}) WHERE refit"
    )
    rows = ch.query(sql, parameters={"agency_id": agency_id, **params, **join.params(between=between)}).result_rows
    return {row[0] for row in rows}
