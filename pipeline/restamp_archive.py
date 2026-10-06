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
second run moves nothing. Rows from the live path (pipeline.clickhouse.
LIVE_SOURCED) are never touched.

Ingest also refuses a file whose stamp lands on a day that has not closed or
that already holds promoted live rows, since a day in `updates` has one
source. A row the restamp would move onto such a day is left out the same
way; it stays in the backup.

captured_at is in the table's sort and partition keys, so it cannot be
mutated in place. The corrected rows are written to a copy, the copy is
swapped in with EXCHANGE TABLES, and the rows as they were stay in
`updates_before_restamp` until an operator drops that table.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.clickhouse import LIVE_SOURCED

_TABLE = "updates"
_STAGING = "updates_restamp_staging"
BACKUP_TABLE = "updates_before_restamp"
# Where the swapped-out table goes when it holds rows the copy never saw,
# written by something that ignored the pipeline lock. No backup is made then,
# so it is the only copy of the rows as they were.
LEFTOVER_TABLE = "updates_restamp_leftover"
# The copy's table comment: this prefix plus the row count `updates` had
# when the copy was taken, which a resumed run checks the swapped-out table
# against.
_COPY_MARK = "restamp copy of "

_HEADER = "toDateTime64(assumeNotNull(feed_timestamp), 0, 'UTC')"
_NAME_DAY = "toDate(parseDateTimeOrNull(substring(file_name, 1, 8), '%Y%m%d'))"
# The header is used only where archive_captured_at would use it: a positive
# timestamp that converts to a datetime, on the name's JST day or the next
# one, or under a name with no date directory at all. The upper bound is the
# column type's last second (2299-12-31); a header past it stays unused. Days
# are Date32, whose range covers that bound, where a Date would wrap.
_HAS_HEADER = "ifNull(feed_timestamp, 0) BETWEEN 1 AND 10413791999"
_RESTAMPED = f"""multiIf(
    {LIVE_SOURCED} OR NOT ({_HAS_HEADER}), captured_at,
    startsWith(file_name, '/'), {_HEADER},
    match(file_name, '^[0-9]{{8}}/')
        AND {_NAME_DAY} IS NOT NULL
        AND dateDiff('day', {_NAME_DAY}, toDate32({_HEADER}, 'Asia/Tokyo')) BETWEEN 0 AND 1,
        {_HEADER},
    captured_at)"""
_NEW_DAY = f"toDate32({_RESTAMPED}, 'Asia/Tokyo')"
_REFUSED = f"""({_NEW_DAY} != toDate32(captured_at, 'Asia/Tokyo') AND (
    {_NEW_DAY} >= toDate32(now(), 'Asia/Tokyo')
    OR (agency_id, {_NEW_DAY}) IN (
        SELECT DISTINCT agency_id, toDate32(captured_at, 'Asia/Tokyo') FROM {_TABLE} WHERE {LIVE_SOURCED})))"""


@dataclass(frozen=True)
class RestampPlan:
    agency_id: int
    archive_rows: int
    rows_to_move: int
    min_shift_sec: int
    max_shift_sec: int
    # Rows that would move onto a day that has not closed or that holds
    # promoted live rows, which the restamp leaves out as ingest would.
    rows_refused: int
    # Archive rows whose name has neither an 8-digit date directory nor an
    # empty one: the rule leaves them as they are, which is what ingest does.
    unreadable_names: int


def plan_restamp(client) -> list[RestampPlan]:
    """Per agency, how many archive rows the restamp would move and how far,
    and how many it would leave out. Reads only."""
    result = client.query(
        f"""
        SELECT agency_id, count(),
               countIf(shift != 0 AND NOT refused),
               minIf(shift, shift != 0 AND NOT refused), maxIf(shift, shift != 0 AND NOT refused),
               countIf(refused),
               countIf(NOT match(file_name, '^[0-9]{{8}}/') AND NOT startsWith(file_name, '/'))
        FROM (
            SELECT agency_id, file_name, dateDiff('second', captured_at, {_RESTAMPED}) AS shift, {_REFUSED} AS refused
            FROM {_TABLE}
            WHERE NOT {LIVE_SOURCED}
        )
        GROUP BY agency_id
        ORDER BY agency_id
        """
    )
    return [
        RestampPlan(int(a), int(n), int(moved), int(lo) if moved else 0, int(hi) if moved else 0, int(out), int(bad))
        for a, n, moved, lo, hi, out, bad in result.result_rows
    ]


def _row_count(client, table: str) -> int:
    """What a run checks for concurrent writes. A count is enough: nothing
    updates or deletes `updates` rows, so a write can only add some, and
    ClickHouse answers an unfiltered count from part metadata."""
    return int(client.query(f"SELECT count() FROM {table}").result_rows[0][0])


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


def _finish_swap(client, before: int) -> None:
    """Keep the swapped-out table, unless it holds rows the copy never saw:
    a write in the instant between the copy's check and the swap. Then it is
    kept as LEFTOVER_TABLE and the run fails, so those rows are never dropped."""
    if _row_count(client, _STAGING) != before:
        client.command(f"RENAME TABLE {_STAGING} TO {LEFTOVER_TABLE}")
        client.command(f"ALTER TABLE {_TABLE} MODIFY COMMENT ''")
        raise RuntimeError(
            f"rows reached {_TABLE} during the swap from a writer that ignores the pipeline lock. The table as it "
            f"was, those rows included, is kept as {LEFTOVER_TABLE} and no backup was made. Stop that writer and "
            f"restore the rows {_TABLE} lacks from {LEFTOVER_TABLE} before anything else"
        )
    _keep_swapped_out(client)


def _marked_count(comment: str | None) -> int | None:
    if not comment or not comment.startswith(_COPY_MARK):
        return None
    return int(comment[len(_COPY_MARK) :])


def leftover_exists(client) -> bool:
    return _exists(client, LEFTOVER_TABLE)


def interrupted_run(client) -> bool:
    """Whether an earlier run stopped partway, leaving a copy or a mark.
    Reads only."""
    return _exists(client, _STAGING) or _marked_count(_table_comment(client, _TABLE)) is not None


def settle_interrupted_run(client) -> bool:
    """Finish or undo whatever an interrupted run left behind.

    The copy is created carrying _COPY_MARK as its table comment, and EXCHANGE
    TABLES swaps whole tables, comment and all, so the mark says where a run
    stopped whatever the tables hold: on `updates`, the swap happened and the
    staging table, if still there, holds the rows as they were, checked
    against the row count the mark records; otherwise the staging table is a
    copy, possibly partial, and is dropped. Returns whether it finished a run
    whose swap had happened.
    """
    before = _marked_count(_table_comment(client, _TABLE))
    if before is not None:
        if _exists(client, _STAGING):
            _finish_swap(client, before)
        else:
            client.command(f"ALTER TABLE {_TABLE} MODIFY COMMENT ''")
        return True
    if _exists(client, _STAGING):
        client.command(f"DROP TABLE {_STAGING}")
    return False


def preflight(client) -> None:
    """Refuse a run that cannot finish: a leftover table still holds rows
    `updates` lacks, or the disk cannot hold the table twice, which the copy
    needs until the swap and the backup's drop."""
    if leftover_exists(client):
        raise RuntimeError(f"{LEFTOVER_TABLE} exists; restore the rows {_TABLE} lacks from it first")
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

    Run it holding the pipeline lock, as the CLI does. A write while the copy is made aborts before
    the swap. One in the instant between that check and the swap leaves the
    swapped-out table with a row `updates` lacks; it is kept as
    LEFTOVER_TABLE and the run fails, never dropped. When BACKUP_TABLE already
    holds an earlier run's original rows it is kept and this run's swapped-out
    copy, the same rows, is dropped, so the backup is always the table before
    any restamp.
    """
    settle_interrupted_run(client)
    preflight(client)
    before = _row_count(client, _TABLE)
    refused = int(client.query(f"SELECT countIf({_REFUSED}) FROM {_TABLE}").result_rows[0][0])
    client.command(f"CREATE TABLE {_STAGING} AS {_TABLE}")
    client.command(f"ALTER TABLE {_STAGING} MODIFY COMMENT '{_COPY_MARK}{before}'")
    try:
        # Filtered in the inner query: in the outer one `captured_at` names
        # the restamped value, so a refusal tested there would see no move.
        client.command(
            f"INSERT INTO {_STAGING} SELECT * REPLACE ({_RESTAMPED} AS captured_at)"
            f" FROM (SELECT * FROM {_TABLE} WHERE NOT {_REFUSED})"
        )
        if _row_count(client, _TABLE) != before:
            raise RuntimeError(f"{_TABLE} changed while the copy was written; nothing was swapped")
        staged = _row_count(client, _STAGING)
        if staged != before - refused:
            raise RuntimeError(f"copy holds {staged} rows, expected {before - refused}; nothing was swapped")
    except Exception:
        client.command(f"DROP TABLE IF EXISTS {_STAGING}")
        raise
    client.command(f"EXCHANGE TABLES {_TABLE} AND {_STAGING}")
    _finish_swap(client, before)
