"""Every timetable an agency loads, kept in ClickHouse under its content.

analyze joins a day's rows to the timetable in force on their service date
(pipeline.timetable_in_force), or to the one they were ingested against where
none is known to be, and either may no longer be loaded. So each timetable is
kept here before it is replaced (load_static) and again by analyze while it is
loaded (a backstop for one loaded before this existed): its stop times, and
its schedule as ingest stamps a poll matching each stop time. Content, not the
version name, is the key: a reload under a new name with the same content
stores nothing new, and the version map says which content each name carried.
The days each name covers are kept beside the map, as its span.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from enum import Enum
from typing import NamedTuple

from pipeline.clickhouse import UPDATE_COLUMNS, UPDATES_TABLE, checked_table
from pipeline.service_day import service_date_ch
from pipeline.static_content import static_parts
from pipeline.timetable_in_force import Segment, Version, gtfs_date, in_force, schedule_row, version_span

logger = logging.getLogger(__name__)

ARCHIVE = "static_stop_times_archive"
SCHEDULE = "static_schedule_archive"
VERSION_MAP = "static_version_map"
SPANS = "static_version_spans"
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


def archive_loaded(conn, ch, agency_id: int, parts: list[str] | None = None) -> str | None:
    """Archive the agency's loaded timetable unless its content is already kept,
    map its version name to that content, record the name's span unless one is
    recorded, and return the content's key.

    None when no stop times are loaded, or when the timetable changed while it
    was being copied (a concurrent load_static committed): nothing is swapped
    in then, and the next call archives what is loaded. A returned key does
    not promise the mapping: a name read after the timetable changed is left
    unmapped, for the next call to record. *parts*, when the
    caller already read them, saves reading them again; they must be the
    loaded timetable's.
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
    _map(conn, ch, agency_id, key, parts)
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


def scheduled_contents(ch, agency_id: int) -> set[str]:
    """The agency's contents whose schedule the archive keeps."""
    rows = ch.query(
        f"SELECT DISTINCT content_hash FROM {SCHEDULE} WHERE agency_id = {{agency:Int32}}",
        parameters={"agency": agency_id},
    ).result_rows
    return {row[0] for row in rows}


def spans(ch, agency_id: int) -> dict[str, tuple[date, date]]:
    """Each of the agency's version names with a recorded span, at its latest one."""
    rows = ch.query(
        f"SELECT static_version_id, argMax(tuple(first_day, last_day), recorded_at) FROM {SPANS} "
        "WHERE agency_id = {agency:Int32} GROUP BY static_version_id",
        parameters={"agency": agency_id},
    ).result_rows
    return {stem: (span[0], span[1]) for stem, span in rows}


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


def clear_archive_joined_states(conn, agency_id: int) -> None:
    """Clear the states of the agency's tables that read the archive, so its next analyze rebuilds them in full."""
    # agg_registry imports ch_aggregates, which imports this module: imported here, at call time.
    from pipeline import agg_registry
    from pipeline.agg_registry import Timetable
    from pipeline.locks import agency_ingest_lock

    names = [
        name for name, table in agg_registry.TABLES.items() if table.timetable is Timetable.ARCHIVED or table.in_force
    ]
    # Under the agency lock: an analyze in flight would otherwise record these
    # states again at its end, as built, and the rebuild would be lost. Within
    # analyze itself the lock is the session's own, taken again.
    with agency_ingest_lock(conn, agency_id):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agg_table_state WHERE agency_id = %s AND table_name = ANY(%s)", (agency_id, names))
        conn.commit()


def _map(conn, ch, agency_id: int, key: str, parts: list[str]) -> None:
    """Record that the loaded version name carries *key*, unless that is already its latest mapping.

    A name already mapped to other content (a timetable refetched under the
    same name) leaves the dates ingested under it built against the other one,
    so the archive-joined tables' states are cleared and they rebuild in full.
    """
    stem = loaded_stem(conn, agency_id)
    if stem is None:
        return
    latest = ch.query(
        f"SELECT argMax(content_hash, archived_at) FROM {VERSION_MAP} "
        "WHERE agency_id = {agency:Int32} AND static_version_id = {stem:String}",
        parameters={"agency": agency_id, "stem": stem},
    ).result_rows
    # argMax over no rows is '', which no content is.
    previous = latest[0][0] if latest else ""
    if previous == key:
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
    ch.insert(
        VERSION_MAP,
        [(agency_id, stem, key, datetime.now(timezone.utc))],
        column_names=["agency_id", "static_version_id", "content_hash", "archived_at"],
    )


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
    rows = ch.query(
        f"SELECT static_version_id, argMax(content_hash, archived_at) FROM {VERSION_MAP} "
        "WHERE agency_id = {agency:Int32} GROUP BY static_version_id ORDER BY static_version_id",
        parameters={"agency": agency_id},
    ).result_rows
    stems, hashes = [row[0] for row in rows], [row[1] for row in rows]
    force: tuple[Segment, ...] = ()
    if by_service_date:
        mapped = dict(zip(stems, hashes, strict=True))
        kept = scheduled_contents(ch, agency_id)
        versions = [Version(stem, *span) for stem, span in spans(ch, agency_id).items() if stem in mapped]
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


def _marked_rows(where: str, table: str) -> str:
    """*table*'s rows matching *where*, each with its place among the segments
    in force, and whether REFIT reads its schedule from the archive."""
    return (
        f"SELECT u.*, {_FORCE_AT} AS force_at, {{force_contents:Array(String)}}[force_at] AS force_content, "
        f"force_content != '' AND force_content != {_STAMPED_CONTENT} AS refit, "
        "if(refit, force_content, '') AS refit_content "
        f"FROM {checked_table(table)} AS u WHERE {where}"
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


def observation_rows(where: str, stamp: Stamp, table: str = UPDATES_TABLE) -> str:
    """The FROM clause of a read of *table*'s rows matching *where*, aliased
    `u`, with `updates`' columns, each row filed under *stamp*'s timetable.

    *where* names columns as `u.` columns. Under INGESTED the clause is the
    table itself; otherwise a subquery that binds ScheduleJoin.params, which
    files a row only by the segments in force. REFIT also joins the schedule
    archive, so a read whose rows need none of it (refit_dates) is cheaper
    under IN_FORCE, with the same result.
    """
    if stamp is Stamp.INGESTED:
        return f"FROM {checked_table(table)} AS u WHERE {where}"
    marked = _marked_rows(where, table)
    if stamp is Stamp.IN_FORCE:
        return f"FROM (SELECT {_filed_columns(False)} FROM ({marked}) AS u) AS u"
    return (
        f"FROM (SELECT {_filed_columns(True)} FROM ({marked}) AS u "
        "ANY LEFT JOIN (SELECT content_hash, trip_id, stop_sequence, service_type, route_id, scheduled_time, "
        f"scheduled_sec FROM {SCHEDULE} WHERE agency_id = {{agency_id:UInt16}} "
        "AND has({force_contents:Array(String)}, content_hash)) AS s "
        "ON s.content_hash = u.refit_content AND s.trip_id = u.trip_id "
        "AND s.stop_sequence = toInt32(u.stop_sequence)) AS u"
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
