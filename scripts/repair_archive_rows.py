"""Repair archive-ingested `updates` rows: their captured_at, and the
timetable fields a static_join agency's rows were ingested without.

Each archive row (file_name outside pipeline.clickhouse.LIVE_SOURCED) gets
what archive ingest assigns it today:

- captured_at, by pipeline.restamp_archive.RESTAMPED, the SQL form of
  archive_captured_at, from the row's own file name and feed_timestamp. A row
  already stamped by that rule keeps its stamp.
- service_type, scheduled_time, scheduled_sec and static_version_id, for an
  agency whose ingest_strategy is static_join and a row whose four fields are
  all NULL, which is how a row that missed the timetable join is stored.
  Each such row takes what static_join.parse_feed resolves for its
  (agency_id, trip_id, stop_sequence) from the timetable loaded in Postgres,
  or else from the first --extra-static zip that has the key. The values are
  derived in Python by parse_departure_time, the parse ingest uses, so the
  extended-hour and malformed cases come out as ingest stores them. A key no
  timetable has stays NULL.

Every other field, and every live-path row, is copied unchanged. route_code
is never changed, although parse_feed takes a missing one from
static_trips.route_id: the dry run counts the archive rows with none.

captured_at is in the table's sort and partition keys, so the rows cannot be
mutated in place. --apply writes the repaired rows into `updates_repair`,
created AS `updates` (same columns, engine, keys and settings), one destination
partition per INSERT, checks the copy against `updates`, and swaps it in with
EXCHANGE TABLES. The rows as they were then stay in `updates_repair` until an
operator drops it. The tool never drops, truncates or deletes anything.

Table comments record how far a run got, and EXCHANGE TABLES swaps them with
the tables: WORK_MARK on `updates_repair` means a copy in progress, WORK_MARK
on `updates` means the swap happened and only the bookkeeping is left, and
BACKUP_MARK on `updates_repair` means a finished repair whose backup is still
there. A run that stops anywhere is finished by rerunning with --resume.

The dry run (the default) only reads: ClickHouse through a readonly=1
client, Postgres through a read-only session. Neither mode writes Postgres.
--apply holds the pipeline's advisory locks (pipeline.locks) for its whole
run, so no ingest, promotion or analyze writes or reads `updates` across the
swap, and it needs --yes or a typed confirmation of the target database.

Usage:
    poetry run python scripts/repair_archive_rows.py
    poetry run python scripts/repair_archive_rows.py --extra-static 8=old/gtfs_static_20260816.zip
    poetry run python scripts/repair_archive_rows.py --apply [--resume] [--agency 8 ...]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clickhouse_connect  # noqa: E402  (sys.path injection above)
import psycopg2  # noqa: E402
from clickhouse_connect.driver.external import ExternalData  # noqa: E402

from pipeline import restamp_archive  # noqa: E402
from pipeline.clickhouse import LIVE_SOURCED, ch_conn_kwargs, jst_date  # noqa: E402
from pipeline.locks import agency_ingest_lock, held_pipeline_locks, try_lock_ingest_analyze  # noqa: E402
from pipeline.static_loader import read_static_rows, resolve_static_zip  # noqa: E402
from pipeline.strategies._time import parse_departure_time  # noqa: E402

TABLE = "updates"
WORK_TABLE = "updates_repair"
WORK_MARK = "archive repair work copy of "
BACKUP_MARK = "archive repair backup: the rows as they were"

# The agencies.ingest_strategy that names pipeline.strategies.static_join.
_JOIN_STRATEGY = "static_join"
_FILLED = ("service_type", "scheduled_time", "scheduled_sec", "static_version_id")
# Destination partitions are computed as toYYYYMM of the repaired stamp, which
# holds only while the table is partitioned that way.
_PARTITION_KEY = "toYYYYMM(captured_at)"
_KEYMAP = "keymap"
_KEYMAP_STRUCTURE = [
    "agency_id UInt16",
    "trip_id String",
    "stop_sequence UInt16",
    "hit UInt8",
    "service_type Nullable(String)",
    "scheduled_time Nullable(String)",
    "scheduled_sec Nullable(Int32)",
    "static_version_id Nullable(String)",
]
# A copy of a large partition outlasts clickhouse-connect's default read
# timeout, and an INSERT the client stops waiting for keeps running on the
# server, so the run would misread a copy still in progress as a failure.
_TIMEOUT_SEC = 24 * 3600
# Postgres reads an INTEGER column's text this way: optional sign, ASCII
# digits, surrounding whitespace.
_PG_INTEGER = re.compile(r"^\s*[+-]?[0-9]+\s*$")


class Refusal(Exception):
    """A run that must not go ahead, with what the operator should do."""


# ── timetable key map ─────────────────────────────────────────────────────────

Key = tuple[int, str, int]
Values = tuple[str | None, str | None, int | None, str | None]


# TabSeparated's escapes for the characters that would otherwise end a field
# or a row, or be read as one; \\N alone is NULL.
_TSV_ESCAPES = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r", "\0": "\\0"})


def _tsv(value: object) -> str:
    return "\\N" if value is None else str(value).translate(_TSV_ESCAPES)


def _values(departure: str | None, service_id: str | None, version: str | None) -> Values:
    scheduled_time, _status, scheduled_sec = parse_departure_time(departure)
    return service_id, scheduled_time, scheduled_sec, version


@dataclass
class KeyMap:
    """What parse_feed resolves per (agency_id, trip_id, stop_sequence). A
    trip takes every stop from the first timetable that has it, Postgres's
    first, as parse_feed reads one timetable: no trip mixes two versions."""

    values: dict[Key, Values] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    _encoded: bytes | None = None

    def add(self, key: Key, values: Values) -> None:
        if 0 <= key[2] <= 0xFFFF:
            self.values.setdefault(key, values)
            self._encoded = None

    def external(self) -> ExternalData:
        """The key map as the external table `keymap` of one query."""
        if self._encoded is None:
            self._encoded = self._encode()
        return ExternalData(file_name=_KEYMAP, data=self._encoded, fmt="TabSeparated", structure=_KEYMAP_STRUCTURE)

    def fingerprint(self, scope: Scope) -> str:
        """A short digest of what a copy is built from: the key map and the
        scope. A resume must match the run it finishes, or the work table
        would hold partitions filled from two inputs."""
        if self._encoded is None:
            self._encoded = self._encode()
        digest = hashlib.sha256(repr((scope.agencies, scope.fill)).encode())
        # Sorted: the timetable rows arrive in no fixed order.
        for line in sorted(self._encoded.split(b"\n")):
            digest.update(line + b"\n")
        return digest.hexdigest()[:16]

    def _encode(self) -> bytes:
        rows = ((a, t, s, 1, svc, sched, sec, ver) for (a, t, s), (svc, sched, sec, ver) in self.values.items())
        return "\n".join("\t".join(map(_tsv, row)) for row in rows).encode()


def join_agencies(conn, scope: list[int] | None) -> list[int]:
    """The static_join agencies among *scope* (every agency when None)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id FROM agencies WHERE ingest_strategy = %s AND (%s::int[] IS NULL OR agency_id = ANY(%s))"
            " ORDER BY agency_id",
            (_JOIN_STRATEGY, scope, scope),
        )
        return [r[0] for r in cur.fetchall()]


def _pg_integer(text: str | None) -> int | None:
    return int(text) if text is not None and _PG_INTEGER.match(text) else None


def read_timetable_zip(path: str) -> tuple[str, dict[str, str | None], list[tuple[str, int, str | None]]]:
    """A GTFS zip's version id, its trips' service_id, and its stop times as
    (trip_id, stop_sequence, departure_time), read as load_static would store
    them: the zip's stem as static_version_id, raw text otherwise, the first
    row of a repeated key kept as its ON CONFLICT DO NOTHING keeps it, and
    rows the static_* columns could not hold left out."""
    p = resolve_static_zip(path)
    with zipfile.ZipFile(p) as zf:
        missing = {"trips.txt", "stop_times.txt"} - set(zf.namelist())
        if missing:
            raise Refusal(f"{p} has no {', '.join(sorted(missing))}")
        trips: dict[str, str | None] = {}
        for trip_id, _route, _headsign, _shape, service_id, _direction in read_static_rows(zf, "trips.txt"):
            if trip_id is not None:
                trips.setdefault(trip_id, service_id)
        stop_times: dict[tuple[str, int], str | None] = {}
        for trip_id, sequence, _stop, _arrival, departure in read_static_rows(zf, "stop_times.txt"):
            seq = _pg_integer(sequence)
            if trip_id is not None and seq is not None:
                stop_times.setdefault((trip_id, seq), departure)
    return p.stem, trips, [(t, s, d) for (t, s), d in stop_times.items()]


def build_keymap(conn, agencies: list[int], extras: list[tuple[int, str]]) -> KeyMap:
    keymap = KeyMap()
    # (agency_id, trip_id) an earlier timetable already supplies.
    owned: set[tuple[int, str]] = set()
    if agencies:
        keymap.sources.append("Postgres static_stop_times/static_trips")
        with conn.cursor(name="repair_keymap") as cur:
            cur.itersize = 50_000
            cur.execute(
                "SELECT st.agency_id, st.trip_id, st.stop_sequence, st.departure_time, t.service_id, "
                "t.static_version_id FROM static_stop_times st "
                "JOIN static_trips t ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id "
                "WHERE st.agency_id = ANY(%s)",
                (agencies,),
            )
            for agency_id, trip_id, seq, departure, service_id, version in cur:
                keymap.add((agency_id, trip_id, seq), _values(departure, service_id, version))
                owned.add((agency_id, trip_id))
        conn.rollback()
    for agency_id, path in extras:
        version, trips, stop_times = read_timetable_zip(path)
        keymap.sources.append(f"agency {agency_id}: {version}")
        supplied = {t for t in trips if (agency_id, t) not in owned}
        for trip_id, seq, departure in stop_times:
            if trip_id in supplied:
                keymap.add((agency_id, trip_id, seq), _values(departure, trips[trip_id], version))
        owned.update((agency_id, t) for t, _seq, _dep in stop_times if t in supplied)
    return keymap


# ── the repair as SQL ─────────────────────────────────────────────────────────


def _ids(ids: list[int]) -> str:
    return ", ".join(str(int(i)) for i in ids)


@dataclass(frozen=True)
class Scope:
    """Which rows the repair may change. *agencies* None means every agency;
    *fill* is the static_join agencies among them."""

    agencies: list[int] | None
    fill: list[int]

    @property
    def targeted(self) -> str:
        """Archive rows of an agency in scope: the only rows any field changes on."""
        if self.agencies is None:
            return f"(NOT {LIVE_SOURCED})"
        return f"(NOT {LIVE_SOURCED} AND agency_id IN ({_ids(self.agencies)}))"

    @property
    def captured_at(self) -> str:
        return f"if({self.targeted}, {restamp_archive.RESTAMPED}, captured_at)"

    @property
    def fills(self) -> str:
        if not self.fill:
            return "0"
        blank = " AND ".join(f"{c} IS NULL" for c in _FILLED)
        return f"(NOT {LIVE_SOURCED} AND agency_id IN ({_ids(self.fill)}) AND {blank})"


def _columns(client) -> list[tuple[str, str]]:
    return [
        (name, typ)
        for name, typ in client.query(
            "SELECT name, type FROM system.columns WHERE database = currentDatabase() AND table = {t:String}"
            " ORDER BY position",
            parameters={"t": TABLE},
        ).result_rows
    ]


def _repaired(columns: list[tuple[str, str]], scope: Scope, where: str) -> tuple[str, str]:
    """The repaired row's expressions in column order, each cast to its
    column's type so a checksum over them compares with one over the table,
    and the FROM clause they read. The stamp and fill are computed in an inner
    query under names no column has: an alias named after a column would
    replace that column wherever the query names it."""
    exprs = []
    for name, typ in columns:
        if name == "captured_at":
            expr = "u.repaired_captured_at"
        elif name in _FILLED:
            expr = f"if(u.repair_fills AND k.hit = 1, k.{name}, u.{name})"
        else:
            expr = f"u.{name}"
        exprs.append(f"CAST({expr} AS {typ})")
    source = (
        f"(SELECT *, {scope.captured_at} AS repaired_captured_at, {scope.fills} AS repair_fills"
        f" FROM {TABLE} WHERE {where}) AS u"
        f" LEFT JOIN {_KEYMAP} AS k"
        " ON u.agency_id = k.agency_id AND u.trip_id = k.trip_id AND u.stop_sequence = k.stop_sequence"
    )
    return ", ".join(exprs), source


def _checksum(columns: list[str]) -> str:
    """An order-free checksum of a row multiset: sum wraps, and unlike xor it
    sees a duplicated row."""
    return f"sumWithOverflow(cityHash64(tuple({', '.join(columns)})))"


# ── the plan (reads only) ─────────────────────────────────────────────────────


@dataclass
class AgencyReport:
    agency_id: int
    fills: bool
    archive_rows: int = 0
    rows_moving: int = 0
    rows_refused: int = 0
    blank_rows: int = 0
    null_route_rows: int = 0
    # floor(shift / 1 h) -> rows, moving rows only
    shift_hours: dict[int, int] = field(default_factory=dict)
    # static_version_id that would fill -> rows
    matched: dict[str | None, int] = field(default_factory=dict)

    @property
    def rows_filled(self) -> int:
        return sum(self.matched.values())


@dataclass(frozen=True)
class Partition:
    partition_id: str
    sources: list[str]
    rows: int


@dataclass
class Plan:
    agencies: list[AgencyReport]
    partitions: list[Partition]
    total_rows: int
    bytes_on_disk: int
    bytes_uncompressed: int
    free_bytes: int | None

    @property
    def changes(self) -> int:
        return sum(a.rows_moving + a.rows_filled for a in self.agencies)

    @property
    def refused(self) -> int:
        return sum(a.rows_refused for a in self.agencies)


def plan(client, sc: Scope, keymap: KeyMap) -> Plan:
    """What --apply would change, from `updates` as it is now. Reads only."""
    key = client.query(
        "SELECT partition_key FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        parameters={"t": TABLE},
    ).result_rows
    if not key or key[0][0] != _PARTITION_KEY:
        raise Refusal(f"{TABLE} is not partitioned by {_PARTITION_KEY}, which the per-partition copy assumes")
    refused = restamp_archive.refusal_predicate(jst_date(datetime.now(timezone.utc)))
    reports: dict[int, AgencyReport] = {}
    for agency_id, rows, moving, out, blank, no_route in client.query(
        f"""
        SELECT agency_id, count(), countIf(shift != 0), countIf(refused), countIf(fills), countIf(route_code IS NULL)
        FROM (
            SELECT agency_id, route_code, dateDiff('second', captured_at, {restamp_archive.RESTAMPED}) AS shift,
                   {refused} AS refused, {sc.fills} AS fills
            FROM {TABLE} WHERE {sc.targeted}
        )
        GROUP BY agency_id ORDER BY agency_id
        """
    ).result_rows:
        reports[agency_id] = AgencyReport(
            agency_id,
            fills=agency_id in sc.fill,
            archive_rows=rows,
            rows_moving=moving,
            rows_refused=out,
            blank_rows=blank,
            null_route_rows=no_route,
        )
    for agency_id, hour, rows in client.query(
        f"""
        SELECT agency_id, toInt64(floor(shift / 3600)) AS hour, count()
        FROM (SELECT agency_id, dateDiff('second', captured_at, {restamp_archive.RESTAMPED}) AS shift
              FROM {TABLE} WHERE {sc.targeted})
        WHERE shift != 0
        GROUP BY agency_id, hour ORDER BY agency_id, hour
        """
    ).result_rows:
        reports[agency_id].shift_hours[hour] = rows
    for agency_id, version, rows in client.query(
        f"""
        SELECT u.agency_id, k.static_version_id, count()
        FROM (SELECT agency_id, trip_id, stop_sequence FROM {TABLE} WHERE {sc.fills}) AS u
        INNER JOIN {_KEYMAP} AS k
            ON u.agency_id = k.agency_id AND u.trip_id = k.trip_id AND u.stop_sequence = k.stop_sequence
        GROUP BY u.agency_id, k.static_version_id ORDER BY u.agency_id, k.static_version_id
        """,
        external_data=keymap.external(),
    ).result_rows:
        reports[agency_id].matched[version] = rows
    partitions = [
        Partition(str(dest), list(sources), rows)
        for dest, sources, rows in client.query(
            f"""
            SELECT toYYYYMM(repaired_captured_at) AS dest, arraySort(groupUniqArray(pid)), count()
            FROM (SELECT _partition_id AS pid, {sc.captured_at} AS repaired_captured_at FROM {TABLE})
            GROUP BY dest ORDER BY dest
            """
        ).result_rows
    ]
    on_disk, uncompressed = client.query(
        "SELECT sum(bytes_on_disk), sum(data_uncompressed_bytes) FROM system.parts"
        " WHERE active AND database = currentDatabase() AND table = {t:String}",
        parameters={"t": TABLE},
    ).result_rows[0]
    try:
        free = int(client.query("SELECT min(free_space) FROM system.disks").result_rows[0][0])
    except Exception:
        free = None
    return Plan(
        list(reports.values()),
        partitions,
        sum(p.rows for p in partitions),
        int(on_disk or 0),
        int(uncompressed or 0),
        free,
    )


def _size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:,.2f} {unit}"
        n /= 1024
    return f"{n:,.2f} TiB"


def _hours(h: int) -> str:
    return f"[{h:+d}h,{h + 1:+d}h)"


def print_plan(p: Plan, keymap: KeyMap) -> None:
    print(f"timetables: {'; '.join(keymap.sources) or 'none (no static_join agency in scope)'}")
    print(f"key map: {len(keymap.values):,} (agency, trip, stop) keys")
    for a in p.agencies:
        what = "captured_at and timetable fields" if a.fills else "captured_at only"
        print(f"agency {a.agency_id} ({what}): {a.archive_rows:,} archive rows, {a.rows_moving:,} would move")
        if a.shift_hours:
            print("  shift: " + " | ".join(f"{_hours(h)} {n:,}" for h, n in sorted(a.shift_hours.items())))
        if a.rows_refused:
            print(f"  {a.rows_refused:,} would land on a day that has not closed or already holds live rows")
        if a.fills:
            by_version = ", ".join(f"{v or 'no version'} {n:,}" for v, n in sorted(a.matched.items(), key=repr))
            share = f" ({a.rows_filled / a.blank_rows:.1%})" if a.blank_rows else ""
            print(
                f"  blank timetable fields: {a.blank_rows:,} rows; {a.rows_filled:,} would fill{share}"
                f"{f' [{by_version}]' if by_version else ''}, {a.blank_rows - a.rows_filled:,} stay blank"
            )
        if a.null_route_rows:
            print(f"  route_code NULL on {a.null_route_rows:,} archive rows (left as is)")
    print(
        f"total: {p.total_rows:,} rows in {TABLE}; {sum(a.rows_moving for a in p.agencies):,} would move, "
        f"{sum(a.rows_filled for a in p.agencies):,} would fill, {p.refused:,} would land on a refused day"
    )
    print(
        "rebuild: "
        + "; ".join(f"partition {q.partition_id} <- {', '.join(q.sources)} ({q.rows:,} rows)" for q in p.partitions)
    )
    free = _size(p.free_bytes) if p.free_bytes is not None else "unknown"
    print(
        f"disk: {TABLE} takes {_size(p.bytes_on_disk)} on disk ({_size(p.bytes_uncompressed)} uncompressed); "
        f"the copy needs about as much again, plus what the filled fields add, and stays as the backup "
        f"until dropped; {free} free"
    )


# ── connections ───────────────────────────────────────────────────────────────


def readonly_client():
    return clickhouse_connect.get_client(
        **ch_conn_kwargs(), settings={"readonly": 1}, send_receive_timeout=_TIMEOUT_SEC
    )


def _write_client():
    return clickhouse_connect.get_client(**ch_conn_kwargs(), send_receive_timeout=_TIMEOUT_SEC)


@contextmanager
def pg_readonly() -> Iterator:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise Refusal("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        conn.set_session(readonly=True)
        yield conn
    finally:
        conn.close()


def _targets() -> tuple[str, str]:
    kw = ch_conn_kwargs()
    pg = urlsplit(os.environ.get("DATABASE_URL", ""))
    return (
        f"database {kw['database']} on {kw['host']}:{kw['port']}",
        f"database {pg.path.lstrip('/')} on {pg.hostname}:{pg.port or 5432}",
    )


# ── apply ─────────────────────────────────────────────────────────────────────


def _comment(client, table: str) -> str | None:
    rows = client.query(
        "SELECT comment FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        parameters={"t": table},
    ).result_rows
    return rows[0][0] if rows else None


def _marked_rows(comment: str | None) -> int | None:
    if not comment or not comment.startswith(WORK_MARK):
        return None
    return int(comment[len(WORK_MARK) :].split()[0])


def _marked_inputs(comment: str | None) -> str | None:
    """The inputs fingerprint a copy's mark records: its last word after "rows"."""
    words = (comment or "")[len(WORK_MARK) :].split()
    return words[2] if len(words) > 2 and words[1] == "rows" else None


def _row_count(client, table: str) -> int:
    return int(client.query(f"SELECT count() FROM {table}").result_rows[0][0])


def _partition_rows(client, table: str) -> dict[str, int]:
    return dict(
        client.query(
            "SELECT partition_id, sum(rows) FROM system.parts"
            " WHERE active AND database = currentDatabase() AND table = {t:String} GROUP BY partition_id",
            parameters={"t": table},
        ).result_rows
    )


def _state(client) -> tuple[str, int | None]:
    """Where an earlier run stopped: ("fresh", None), ("copying", rows) or
    ("swapped", rows), rows being `updates`' count when the copy began.
    Raises Refusal for every other state."""
    if restamp_archive.interrupted_run(client) or restamp_archive.leftover_exists(client):
        raise Refusal(
            "a gtfs_pipeline.py restamp_archive run stopped partway or left rows aside; settle it first "
            "(rerun restamp_archive, which reports what it left)"
        )
    work = _comment(client, WORK_TABLE)
    main = _comment(client, TABLE)
    swapped = _marked_rows(main)
    if swapped is not None:
        if work is None:
            raise Refusal(f"{TABLE} carries the repair's mark but {WORK_TABLE}, the backup, is gone; inspect both")
        return "swapped", swapped
    if main:
        raise Refusal(f"{TABLE} carries a comment this tool did not write ({main!r}); inspect it first")
    if work is None:
        return "fresh", None
    if work.startswith(BACKUP_MARK):
        raise Refusal(
            f"{WORK_TABLE} holds the backup of an earlier repair. Once the aggregates are right, drop it "
            f"(DROP TABLE {WORK_TABLE}) before another run"
        )
    copying = _marked_rows(work)
    if copying is None:
        raise Refusal(f"{WORK_TABLE} exists and carries no repair mark; inspect it")
    return "copying", copying


def _same_ddl(client) -> bool:
    """Whether the two tables' definitions agree but for name and comment."""

    def normal(table: str) -> str:
        ddl = client.query(f"SHOW CREATE TABLE {table}").result_rows[0][0]
        ddl = re.sub(r"^CREATE TABLE \S+", "CREATE TABLE", ddl)
        return re.sub(r"\s*COMMENT '(?:[^'\\]|\\.)*'\s*$", "", ddl)

    return normal(TABLE) == normal(WORK_TABLE)


def _running_copies(client) -> list[str]:
    """Query ids of INSERTs into the work table still running on the server,
    which a client killed mid-copy leaves behind."""
    return [
        r[0]
        for r in client.query(
            "SELECT query_id FROM system.processes WHERE query_id != queryID() AND query ILIKE {pattern:String}",
            parameters={"pattern": f"INSERT INTO {WORK_TABLE}%"},
        ).result_rows
    ]


def _copy(client, scope: Scope, keymap: KeyMap, p: Plan) -> None:
    """Fill the work table one destination partition at a time. A partition
    already complete is skipped, so a rerun picks up where a run stopped; one
    holding only part of its rows is the operator's to clear."""
    columns = _columns(client)
    names = ", ".join(name for name, _ in columns)
    for part in p.partitions:
        have = _partition_rows(client, WORK_TABLE).get(part.partition_id, 0)
        if have == part.rows:
            print(f"partition {part.partition_id}: already copied ({have:,} rows)")
            continue
        if have:
            raise Refusal(
                f"partition {part.partition_id} of {WORK_TABLE} holds {have:,} of its {part.rows:,} rows, from a "
                f"copy that stopped partway. {WORK_TABLE} is still the unswapped work copy, so clear that "
                f"partition with ALTER TABLE {WORK_TABLE} DROP PARTITION ID '{part.partition_id}' and rerun "
                "with --resume"
            )
        if not all(re.fullmatch(r"[0-9A-Za-z_-]+", s) for s in part.sources):
            raise RuntimeError(f"unexpected partition ids {part.sources}")
        sources = ", ".join(f"'{s}'" for s in part.sources)
        where = f"_partition_id IN ({sources}) AND toYYYYMM({scope.captured_at}) = {int(part.partition_id)}"
        exprs, source = _repaired(columns, scope, where)
        print(f"partition {part.partition_id}: copying {part.rows:,} rows from {', '.join(part.sources)}")
        client.command(
            f"INSERT INTO {WORK_TABLE} ({names}) SELECT {exprs} FROM {source}", external_data=keymap.external()
        )
        have = _partition_rows(client, WORK_TABLE).get(part.partition_id, 0)
        if have != part.rows:
            raise RuntimeError(f"partition {part.partition_id} copied {have:,} rows, expected {part.rows:,}")


def verify(client, scope: Scope, keymap: KeyMap, before: int) -> None:
    """Compare the whole work copy with `updates`, per agency: row counts;
    every column of the rows the repair leaves alone; every column it never
    changes on the rest; and the copy against the repair recomputed from
    `updates`. Raises before anything is swapped."""
    columns = _columns(client)
    names = [name for name, _ in columns]
    kept = [n for n in names if n != "captured_at" and n not in _FILLED]

    def per_agency(sql: str, **kwargs) -> dict[int, int]:
        return dict(client.query(sql, **kwargs).result_rows)

    def side_by_side(expr: str, where: str) -> tuple[dict[int, int], dict[int, int]]:
        q = "SELECT agency_id, {e} FROM {t} WHERE {w} GROUP BY agency_id"
        return (
            per_agency(q.format(e=expr, t=TABLE, w=where)),
            per_agency(q.format(e=expr, t=WORK_TABLE, w=where)),
        )

    problems = []
    old, new = side_by_side("count()", "1")
    if old != new or sum(new.values()) != before:
        problems.append(f"row counts differ: {TABLE} {old}, {WORK_TABLE} {new}, expected {before:,} in all")
    old, new = side_by_side(_checksum(names), f"NOT {scope.targeted}")
    if old != new:
        problems.append("rows the repair leaves alone differ")
    old, new = side_by_side(_checksum(kept), scope.targeted)
    if old != new:
        problems.append("columns the repair never changes differ")
    exprs, source = _repaired(columns, scope, "1")
    expected = per_agency(
        f"SELECT u.agency_id, {_checksum([exprs])} FROM {source} GROUP BY u.agency_id",
        external_data=keymap.external(),
    )
    actual = per_agency(f"SELECT agency_id, {_checksum(names)} FROM {WORK_TABLE} GROUP BY agency_id")
    if expected != actual:
        problems.append("the copy differs from the repair recomputed from the original rows")
    if not _same_ddl(client):
        problems.append(f"{WORK_TABLE}'s definition differs from {TABLE}'s")
    if problems:
        raise RuntimeError("verification failed, nothing was swapped: " + "; ".join(problems))
    print(f"verified: {before:,} rows, per-agency counts and checksums agree")


def finalize(client, before: int) -> list[str]:
    """After the swap: mark the swapped-out table as the backup and clear
    `updates`' mark. Returns problems the operator must see."""
    problems = []
    kept = _row_count(client, WORK_TABLE)
    if kept != before:
        problems.append(
            f"{WORK_TABLE} holds {kept:,} rows, but the copy was made from {before:,}: a writer that ignores the "
            f"pipeline lock wrote to {TABLE} during the swap, and those rows are only in {WORK_TABLE}. Copy them "
            f"into {TABLE} before dropping {WORK_TABLE}"
        )
    client.command(f"ALTER TABLE {WORK_TABLE} MODIFY COMMENT '{BACKUP_MARK}'")
    client.command(f"ALTER TABLE {TABLE} MODIFY COMMENT ''")
    return problems


def _next_steps(database: str, scope: list[int] | None) -> None:
    print(f"The rows as they were are in {database}.{WORK_TABLE}.")
    print("Next, rebuild the aggregates of every date. Rows moved between dates and filled in place replace")
    print("others one for one, which the per-date row-count ledger cannot see, so the rebuild must be full:")
    if scope is None:
        print("  make analyze-all FULL=1")
    for agency_id in scope or []:
        print(f"  make analyze AGENCY_ID={agency_id} FULL=1")
    print("  make check-aggs")
    print(f"Once the aggregates are right, drop the backup: DROP TABLE {database}.{WORK_TABLE}")


def _confirm(args, database: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("--apply rewrites the table: pass --yes, or run it from a terminal to confirm", file=sys.stderr)
        return False
    answer = input(f"Type the ClickHouse database name ({database}) to rewrite its {TABLE} table: ")
    return answer.strip() == database


def run_apply(args, sc: Scope, extras: list[tuple[int, str]]) -> int:
    database = ch_conn_kwargs()["database"]
    if not _confirm(args, database):
        return 1
    client = _write_client()
    engine = client.query("SELECT engine FROM system.databases WHERE name = currentDatabase()").result_rows[0][0]
    if engine != "Atomic":
        raise Refusal(f"database {database} uses the {engine} engine; EXCHANGE TABLES needs Atomic")
    with pg_readonly() as conn, ExitStack() as held:
        # Held until `conn` closes: no ingest, promotion or analyze writes
        # `updates`, or reads it across the swap. Each agency's key is taken in
        # ascending order, which pipeline.locks.agency_ingest_lock relies on.
        if not try_lock_ingest_analyze(conn):
            raise Refusal("an ingest or analyze job holds the pipeline lock; rerun once it ends")
        listed = client.query(f"SELECT DISTINCT agency_id FROM {TABLE} ORDER BY agency_id").result_rows
        agencies = [r[0] for r in listed]
        for agency_id in agencies:
            held.enter_context(agency_ingest_lock(conn, agency_id))

        running = _running_copies(client)
        if running:
            raise Refusal(
                f"a copy into {WORK_TABLE} is still running on the server ({', '.join(running)}). Wait for it to "
                "end, or stop it with KILL QUERY WHERE query_id = '...', then rerun"
            )
        state, before = _state(client)
        if state != "fresh" and not args.resume:
            raise Refusal(f"{WORK_TABLE} holds an earlier run that stopped partway; rerun with --resume to finish it")
        if state == "swapped":
            assert before is not None
            print(f"the swap of an earlier run is done; finishing its bookkeeping ({before:,} rows)")
            problems = finalize(client, before)
        else:
            keymap = build_keymap(conn, sc.fill, extras)
            p = plan(client, sc, keymap)
            print_plan(p, keymap)
            if p.refused:
                raise Refusal(
                    f"{p.refused:,} archive rows would move onto a day that has not closed or already holds live "
                    "rows, where ingest keeps one source per day. This tool keeps every row; settle those days "
                    "first (gtfs_pipeline.py restamp_archive leaves such rows out)"
                )
            if state == "fresh":
                if not p.changes:
                    print("nothing to repair.")
                    return 0
                if p.free_bytes is not None and p.free_bytes < p.bytes_on_disk:
                    raise Refusal(f"{_size(p.free_bytes)} free, and the copy needs about {_size(p.bytes_on_disk)}")
                before = p.total_rows
                mark = f"{WORK_MARK}{before} rows {keymap.fingerprint(sc)}"
                client.command(f"CREATE TABLE {WORK_TABLE} AS {TABLE} COMMENT '{mark}'")
            elif _marked_inputs(_comment(client, WORK_TABLE)) != keymap.fingerprint(sc):
                raise Refusal(
                    f"{WORK_TABLE} was begun from different timetables or scope (--agency, --extra-static, or the "
                    "timetables loaded in Postgres). Resume with the interrupted run's options, or drop "
                    f"{WORK_TABLE} (DROP TABLE {WORK_TABLE}) and run again"
                )
            assert before is not None
            if p.total_rows != before:
                raise Refusal(
                    f"{TABLE} holds {p.total_rows:,} rows, and the work copy in {WORK_TABLE} began from {before:,}: "
                    f"it is stale. Drop {WORK_TABLE} (DROP TABLE {WORK_TABLE}) and run again"
                )
            _copy(client, sc, keymap, p)
            verify(client, sc, keymap, before)
            if held_pipeline_locks(conn) < 1 + len(agencies):
                raise RuntimeError("the pipeline lock was lost during the copy; nothing was swapped")
            if _row_count(client, TABLE) != before:
                raise RuntimeError(f"{TABLE} changed while the copy was written; nothing was swapped")
            client.command(f"EXCHANGE TABLES {TABLE} AND {WORK_TABLE}")
            print(f"swapped: {TABLE} now holds the repaired rows")
            problems = finalize(client, before)
    for problem in problems:
        print(problem, file=sys.stderr)
    _next_steps(database, sc.agencies)
    return 1 if problems else 0


def run_dry(sc: Scope, extras: list[tuple[int, str]]) -> int:
    client = readonly_client()
    with pg_readonly() as conn:
        keymap = build_keymap(conn, sc.fill, extras)
    try:
        state, _rows = _state(client)
        if state == "copying" and _marked_inputs(_comment(client, WORK_TABLE)) != keymap.fingerprint(sc):
            print(
                "an earlier --apply run stopped partway, begun from different timetables or scope than these "
                "options; --apply --resume needs the interrupted run's options"
            )
        elif state != "fresh":
            print(f"an earlier --apply run stopped partway ({state}); --apply --resume finishes it")
    except Refusal as why:
        print(f"--apply would refuse: {why}")
    print_plan(plan(client, sc, keymap), keymap)
    print("dry run: nothing written. Rerun with --apply to rebuild.")
    return 0


def _extra(value: str) -> tuple[int, str]:
    agency, sep, path = value.partition("=")
    if not sep or not agency.strip().isdigit() or not path:
        raise argparse.ArgumentTypeError(f"expected AGENCY_ID=ZIP, got {value!r}")
    return int(agency), path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="rebuild `updates` (default: report only)")
    parser.add_argument("--yes", action="store_true", help="skip the --apply confirmation prompt")
    parser.add_argument("--resume", action="store_true", help="finish an --apply run that stopped partway")
    parser.add_argument(
        "--agency",
        type=int,
        action="append",
        metavar="ID",
        help="change only this agency's rows (repeatable); the table is still rewritten whole",
    )
    parser.add_argument(
        "--extra-static",
        type=_extra,
        action="append",
        default=[],
        metavar="AGENCY_ID=ZIP",
        help="an older timetable zip for that agency, for keys today's timetable lacks (repeatable, in priority order)",
    )
    args = parser.parse_args(argv)
    scope = sorted(set(args.agency)) if args.agency else None
    ch_target, pg_target = _targets()
    print(f"ClickHouse: {ch_target}{'' if args.apply else ' (readonly=1)'}")
    print(f"Postgres:   {pg_target} (read-only session)")
    try:
        with pg_readonly() as conn:
            fill = join_agencies(conn, scope)
        for agency_id, _path in args.extra_static:
            if agency_id not in fill:
                raise Refusal(f"--extra-static names agency {agency_id}, which is not a static_join agency in scope")
        sc = Scope(scope, fill)
        return run_apply(args, sc, args.extra_static) if args.apply else run_dry(sc, args.extra_static)
    except Refusal as why:
        print(f"refused: {why}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
