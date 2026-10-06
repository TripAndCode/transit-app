"""Bring archive-ingested `updates` rows to the captured_at archive ingest
assigns today.

Archive ingest stamps a row with its feed's FeedHeader timestamp when that
falls on the archive name's JST day or the next one, and with the name's own
JST reading otherwise (pipeline.strategies._pb.archive_captured_at). A row
stamped from the name alone, where the collector wrote the name in UTC as
rt-poller.sh does, sits nine hours early. This recomputes every archive row's
stamp by that same rule, in SQL, from what ingest stored with the row:
`file_name` begins with the date directory ingest read, and `feed_timestamp`
is the header. A row already stamped by the rule comes out unchanged, so a
second run moves nothing, and live-ingested rows (`live_*`) are never touched.

captured_at is in the table's sort and partition keys, so it cannot be
mutated in place. The corrected rows are written to a copy, the copy is
swapped in with EXCHANGE TABLES, and the rows as they were stay in
`updates_before_restamp` until an operator drops that table.
"""

from __future__ import annotations

from dataclasses import dataclass

_TABLE = "updates"
_STAGING = "updates_restamp_staging"
BACKUP_TABLE = "updates_before_restamp"
# Where the swapped-out table goes when it holds rows the copy never saw.
LEFTOVER_TABLE = "updates_restamp_leftover"
_COPY_MARK = "restamp copy"

_HEADER = "toDateTime64(assumeNotNull(feed_timestamp), 0, 'UTC')"
_NAME_DAY = "toDate(parseDateTimeOrNull(substring(file_name, 1, 8), '%Y%m%d'))"
# The header is used only where archive_captured_at would use it: a positive
# timestamp that converts to a datetime, on the name's JST day or the next
# one, or under a name with no date directory at all. The upper bound is the
# column type's last second (2299-12-31); a header past it stays unused.
_HAS_HEADER = "ifNull(feed_timestamp, 0) BETWEEN 1 AND 10413791999"
_RESTAMPED = f"""multiIf(
    startsWith(file_name, 'live_') OR NOT ({_HAS_HEADER}), captured_at,
    startsWith(file_name, '/'), {_HEADER},
    match(file_name, '^[0-9]{{8}}/')
        AND {_NAME_DAY} IS NOT NULL
        AND dateDiff('day', {_NAME_DAY}, toDate({_HEADER}, 'Asia/Tokyo')) BETWEEN 0 AND 1,
        {_HEADER},
    captured_at)"""


@dataclass(frozen=True)
class RestampPlan:
    agency_id: int
    archive_rows: int
    rows_to_move: int
    min_shift_sec: int
    max_shift_sec: int
    # Archive rows whose name has neither an 8-digit date directory nor an
    # empty one: the rule leaves them as they are, which is what ingest does.
    unreadable_names: int


def plan_restamp(client) -> list[RestampPlan]:
    """Per agency, how many archive rows the restamp would move and how far.
    Reads only."""
    result = client.query(
        f"""
        SELECT agency_id, count(), countIf(shift != 0), minIf(shift, shift != 0), maxIf(shift, shift != 0),
               countIf(NOT match(file_name, '^[0-9]{{8}}/') AND NOT startsWith(file_name, '/'))
        FROM (
            SELECT agency_id, file_name, dateDiff('second', captured_at, {_RESTAMPED}) AS shift
            FROM {_TABLE}
            WHERE NOT startsWith(file_name, 'live_')
        )
        GROUP BY agency_id
        ORDER BY agency_id
        """
    )
    return [
        RestampPlan(int(a), int(n), int(moved), int(lo) if moved else 0, int(hi) if moved else 0, int(bad))
        for a, n, moved, lo, hi, bad in result.result_rows
    ]


def _fingerprint(client, table: str) -> tuple:
    return tuple(
        client.query(
            f"SELECT count(), sum(cityHash64(agency_id, file_name, trip_id, stop_sequence, captured_at)) FROM {table}"
        ).result_rows[0]
    )


def _exists(client, table: str) -> bool:
    return bool(client.query(f"EXISTS TABLE {table}").result_rows[0][0])


def _table_comment(client, table: str) -> str | None:
    rows = client.query(
        "SELECT comment FROM system.tables WHERE database = currentDatabase() AND name = {t:String}",
        parameters={"t": table},
    ).result_rows
    return rows[0][0] if rows else None


def _keep_swapped_out(client) -> None:
    """After a swap the staging table holds the rows as they were. It becomes
    the backup unless an earlier run's backup, the older original, is already
    there; then `updates` sheds the copy's mark."""
    if _exists(client, BACKUP_TABLE):
        client.command(f"DROP TABLE {_STAGING}")
    else:
        client.command(f"RENAME TABLE {_STAGING} TO {BACKUP_TABLE}")
    client.command(f"ALTER TABLE {_TABLE} MODIFY COMMENT ''")


def _settle_interrupted_run(client) -> None:
    """Finish or undo whatever an interrupted run left behind.

    The copy is created carrying _COPY_MARK as its table comment, and EXCHANGE
    TABLES swaps whole tables, comment and all, so the mark says where a run
    stopped whatever the tables hold: on `updates`, the swap happened and the
    staging table, if still there, holds the rows as they were; otherwise the
    staging table is a copy, possibly partial, and is dropped.
    """
    if _table_comment(client, _TABLE) == _COPY_MARK:
        if _exists(client, _STAGING):
            _keep_swapped_out(client)
        else:
            client.command(f"ALTER TABLE {_TABLE} MODIFY COMMENT ''")
    elif _exists(client, _STAGING):
        client.command(f"DROP TABLE {_STAGING}")


def _check_room(client) -> None:
    """The copy is written beside the table, so the disk must hold the table
    twice until the swap and the backup's drop."""
    if _exists(client, LEFTOVER_TABLE):
        raise RuntimeError(f"{LEFTOVER_TABLE} exists; move its rows into {_TABLE} and drop it first")
    size = client.query(
        "SELECT sum(bytes_on_disk) FROM system.parts"
        f" WHERE active AND database = currentDatabase() AND table = '{_TABLE}'"
    ).result_rows[0][0]
    free = client.query("SELECT min(free_space) FROM system.disks").result_rows[0][0]
    if free < size:
        raise RuntimeError(f"{free} bytes free, and the copy of {_TABLE} needs about {size}")


def restamp_archive_rows(client) -> None:
    """Rewrite `updates` with every archive row restamped, keeping the rows as
    they were in BACKUP_TABLE.

    Run it with ingest stopped. A write while the copy is made aborts before
    the swap. One in the instant between that check and the swap leaves the
    swapped-out table with a row `updates` lacks; it is kept as
    LEFTOVER_TABLE and the run fails, never dropped. When BACKUP_TABLE already
    holds an earlier run's original rows it is kept and this run's swapped-out
    copy, the same rows, is dropped, so the backup is always the table before
    any restamp.
    """
    _settle_interrupted_run(client)
    _check_room(client)
    before = _fingerprint(client, _TABLE)
    client.command(f"CREATE TABLE {_STAGING} AS {_TABLE}")
    client.command(f"ALTER TABLE {_STAGING} MODIFY COMMENT '{_COPY_MARK}'")
    try:
        client.command(f"INSERT INTO {_STAGING} SELECT * REPLACE ({_RESTAMPED} AS captured_at) FROM {_TABLE}")
        if _fingerprint(client, _TABLE) != before:
            raise RuntimeError(f"{_TABLE} changed while the copy was written; nothing was swapped")
        staged = client.query(f"SELECT count() FROM {_STAGING}").result_rows[0][0]
        if staged != before[0]:
            raise RuntimeError(f"copy holds {staged} rows, {_TABLE} {before[0]}; nothing was swapped")
    except Exception:
        client.command(f"DROP TABLE IF EXISTS {_STAGING}")
        raise
    client.command(f"EXCHANGE TABLES {_TABLE} AND {_STAGING}")
    if _fingerprint(client, _STAGING) != before:
        client.command(f"RENAME TABLE {_STAGING} TO {LEFTOVER_TABLE}")
        client.command(f"ALTER TABLE {_TABLE} MODIFY COMMENT ''")
        raise RuntimeError(
            f"rows reached {_TABLE} during the swap; the table as it was is in {LEFTOVER_TABLE}. "
            f"Insert its rows missing from {_TABLE}, then drop it"
        )
    _keep_swapped_out(client)
