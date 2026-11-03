"""Every timetable an agency loads, kept in ClickHouse under its content.

analyze joins a day's rows to the timetable they were ingested against, which
may no longer be loaded, so each timetable is kept here before it is replaced
(load_static) and again by analyze while it is loaded (a backstop for one
loaded before this existed). Content, not the version name, is the key: a
reload under a new name with the same content stores nothing new, and the
version map says which content each name carried.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from pipeline.static_content import static_parts

logger = logging.getLogger(__name__)

ARCHIVE = "static_stop_times_archive"
VERSION_MAP = "static_version_map"
_COLUMNS = ["agency_id", "content_hash", "trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"]
_BATCH = 50_000


def content_hash(parts: list[str]) -> str:
    """The archive key for a timetable whose content parts are *parts*."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def require_tables(ch) -> None:
    """Refuse to run against a ClickHouse store that predates the archive."""
    names = {
        row[0] for row in ch.query("SELECT name FROM system.tables WHERE database = currentDatabase()").result_rows
    }
    missing = [table for table in (ARCHIVE, VERSION_MAP) if table not in names]
    if missing:
        raise RuntimeError(f"ClickHouse has no {', '.join(missing)}; apply the schema with `make ch-bootstrap`")


def loaded_stem(conn, agency_id: int) -> str | None:
    """The version name of the agency's loaded timetable, or None for one loaded before names were kept."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(static_version_id) FROM static_trips WHERE agency_id = %s", (agency_id,))
        row = cur.fetchone()
    return row[0] if row else None


def _work_table(agency_id: int, key: str) -> str:
    # Unique per attempt: a load's outgoing archive and an analyze backstop can
    # archive the same content at once, and must not drop each other's table.
    # It ends like an aggregate work table, so ch_aggregates.WORK_TABLE_PATTERN
    # also finds one a killed copy left behind.
    return f"{ARCHIVE}_{key[:8]}_{secrets.token_hex(4)}__work_a{int(agency_id)}"


def _stop_time_count(parts: list[str]) -> int:
    for part in parts:
        table, count, _ = part.split(":")
        if table == "static_stop_times":
            return int(count)
    return 0


def archive_loaded(conn, ch, agency_id: int, parts: list[str] | None = None) -> str | None:
    """Archive the agency's loaded timetable unless its content is already kept,
    map its version name to that content, and return the content's key.

    None when no stop times are loaded, or when the timetable changed while it
    was being copied (a concurrent load_static committed): nothing is swapped
    in then, and the next call archives what is loaded. *parts*, when the
    caller already read them, saves reading them again; they must be the
    loaded timetable's.
    """
    if parts is None:
        parts = static_parts(agency_id, conn, True)
    expected = _stop_time_count(parts)
    if expected == 0:
        return None
    key = content_hash(parts)
    present = ch.query(
        f"SELECT count() FROM {ARCHIVE} WHERE agency_id = {{agency:Int32}} AND content_hash = {{key:String}}",
        parameters={"agency": agency_id, "key": key},
    ).result_rows
    if not (present and present[0][0]) and not _copy(conn, ch, agency_id, key, parts, expected):
        return None
    _map(conn, ch, agency_id, key, parts)
    return key


def _copy(conn, ch, agency_id: int, key: str, parts: list[str], expected: int) -> bool:
    """Copy the loaded stop times into the archive under *key*, swapped in whole,
    unless the timetable no longer matches *parts* once copied."""
    work = _work_table(agency_id, key)
    ch.command(f"CREATE TABLE {work} AS {ARCHIVE}")
    try:
        copied = 0
        with conn.cursor(name=f"archive_{int(agency_id)}_{secrets.token_hex(4)}") as cur:
            cur.itersize = _BATCH
            cur.execute(
                "SELECT trip_id, stop_sequence, stop_id, arrival_time, departure_time "
                "FROM static_stop_times WHERE agency_id = %s",
                (agency_id,),
            )
            batch: list[tuple] = []
            for row in cur:
                batch.append((agency_id, key, *row))
                if len(batch) == _BATCH:
                    ch.insert(work, batch, column_names=_COLUMNS)
                    copied += len(batch)
                    batch = []
            if batch:
                ch.insert(work, batch, column_names=_COLUMNS)
                copied += len(batch)
        # The key was read before the copy, in another statement: a load that
        # committed in between would leave another timetable's rows under it.
        if copied != expected or static_parts(agency_id, conn, True) != parts:
            logger.warning(f"  timetable archive: agency {agency_id}'s timetable changed while copying; not archived")
            return False
        ch.command(f"ALTER TABLE {ARCHIVE} REPLACE PARTITION ({int(agency_id)}, '{key}') FROM {work}")
        logger.info(f"  timetable archive: agency {agency_id} content {key} archived ({copied:,} stop times)")
        return True
    finally:
        ch.command(f"DROP TABLE IF EXISTS {work} SYNC")


def _map(conn, ch, agency_id: int, key: str, parts: list[str]) -> None:
    """Record that the loaded version name carries *key*, unless that is already its latest mapping."""
    stem = loaded_stem(conn, agency_id)
    if stem is None:
        return
    latest = ch.query(
        f"SELECT argMax(content_hash, archived_at) FROM {VERSION_MAP} "
        "WHERE agency_id = {agency:Int32} AND static_version_id = {stem:String}",
        parameters={"agency": agency_id, "stem": stem},
    ).result_rows
    if latest and latest[0][0] == key:
        return
    # A load replaces the name and the content in one commit, so content read
    # after the name still matching *parts* means the name read was *key*'s.
    if static_parts(agency_id, conn, True) != parts:
        logger.warning(f"  timetable archive: agency {agency_id}'s timetable changed before its name was mapped")
        return
    ch.insert(
        VERSION_MAP,
        [(agency_id, stem, key, datetime.now(timezone.utc))],
        column_names=["agency_id", "static_version_id", "content_hash", "archived_at"],
    )
