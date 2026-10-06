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

_HEADER = "fromUnixTimestamp(toUInt32(assumeNotNull(feed_timestamp)))"
_NAME_DAY = "toDate(parseDateTimeOrNull(substring(file_name, 1, 8), '%Y%m%d'))"
# The header is used only where archive_captured_at would use it: a positive
# timestamp that converts to a datetime (UInt32 seconds here), on the name's
# JST day or the next one, or under a name with no date directory at all.
_HAS_HEADER = "ifNull(feed_timestamp, 0) BETWEEN 1 AND 4294967295"
_RESTAMPED = f"""multiIf(
    startsWith(file_name, 'live_') OR NOT ({_HAS_HEADER}), captured_at,
    startsWith(file_name, '/'), toDateTime64({_HEADER}, 0, 'UTC'),
    match(file_name, '^[0-9]{{8}}/')
        AND {_NAME_DAY} IS NOT NULL
        AND dateDiff('day', {_NAME_DAY}, toDate({_HEADER}, 'Asia/Tokyo')) BETWEEN 0 AND 1,
        toDateTime64({_HEADER}, 0, 'UTC'),
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


def restamp_archive_rows(client) -> None:
    """Rewrite `updates` with every archive row restamped, keeping the rows as
    they were in BACKUP_TABLE.

    Refuses to swap if `updates` changed while the copy was being written:
    rows appended during that window would exist only in the old table. Run it
    with ingest stopped. When BACKUP_TABLE already holds an earlier run's
    original rows, it is kept and this run's pre-swap copy is dropped, so the
    backup is always the table before any restamp.
    """
    before = _fingerprint(client, _TABLE)
    client.command(f"DROP TABLE IF EXISTS {_STAGING}")
    client.command(f"CREATE TABLE {_STAGING} AS {_TABLE}")
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
    if client.query(f"EXISTS TABLE {BACKUP_TABLE}").result_rows[0][0]:
        client.command(f"DROP TABLE {_STAGING}")
    else:
        client.command(f"RENAME TABLE {_STAGING} TO {BACKUP_TABLE}")
