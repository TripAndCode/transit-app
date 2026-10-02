"""Daily promotion: each closed JST day moves from `updates_live` into `updates`.

`updates` and every aggregate hold closed JST days only. A day reaches history
once it has ended, by this copy, as exactly the polls the live path collected.

Idempotent per file. A `file_name` is done, and not copied again, only once its
row count in `updates` equals its row count in `updates_live` for that day --
checked by count, not by mere presence, because `INSERT ... SELECT` into a
MergeTree table commits in blocks: a run that fails partway through a file can
leave some of its rows in `updates` without all of them. Presence alone would
then read as done and the gap would go uncopied forever once `updates_live`'s
TTL drops the source rows. A file present in `updates` but short of its live
count instead raises PromotionIncomplete, every run, until the missing rows
are repaired -- that keeps the shortfall visible rather than silently
resolving itself into permanent data loss. A rerun, a poll landing after its
day was promoted, and rows `updates` already held for a day before the split
all reconcile normally on the next run, since each starts with an equal
count. A day has one source: a day for which `updates` holds archive rows is
not promoted (and is logged), the mirror of archive ingest refusing a day
that holds promoted rows.

A day never promoted is lost once `updates_live`'s TTL drops it, so a failure
here raises for the caller to record, and every run retries it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from pipeline.clickhouse import (
    LIVE_TABLE,
    UPDATE_COLUMNS,
    UPDATES_TABLE,
    days_with_source,
    jst_date,
    jst_midnight_utc,
)
from pipeline.locks import agency_ingest_lock

logger = logging.getLogger(__name__)

_COLUMNS = ", ".join(UPDATE_COLUMNS)
_WINDOW = "WHERE agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64} "
_LIVE_DAYS_SQL = (
    f"SELECT DISTINCT toDate(captured_at, 'Asia/Tokyo') AS day FROM {LIVE_TABLE} "
    "WHERE agency_id = {agency_id:UInt16} AND captured_at < {cutoff:DateTime64} ORDER BY day"
)
_LIVE_FILES_SQL = f"SELECT file_name, count() AS n FROM {LIVE_TABLE} " + _WINDOW + "GROUP BY file_name"
_DONE_FILES_SQL = f"SELECT file_name, count() AS n FROM {UPDATES_TABLE} " + _WINDOW + "GROUP BY file_name"
_COPY_SQL = (
    f"INSERT INTO {UPDATES_TABLE} ({_COLUMNS}) SELECT {_COLUMNS} FROM {LIVE_TABLE} "
    + _WINDOW
    + "AND has({files:Array(String)}, file_name)"
)
_COPIED_ROWS_SQL = f"SELECT count() FROM {UPDATES_TABLE} " + _WINDOW + "AND has({files:Array(String)}, file_name)"


class PromotionIncomplete(RuntimeError):
    """Fewer rows reached `updates` than `updates_live` held for the files copied."""


def promote_closed_days(agency_id: int, conn, ch_client, *, now: datetime | None = None) -> int:
    """Copy every closed JST day `updates_live` holds for *agency_id* into
    `updates`, and return the rows copied.

    Holds the agency's `updates` lock for the whole run, as analyze() does, so
    a copy never lands between analyze's reads (pipeline/locks.py).
    """
    today = jst_date(now or datetime.now(timezone.utc))
    with agency_ingest_lock(conn, agency_id):
        days = [
            row[0]
            for row in ch_client.query(
                _LIVE_DAYS_SQL, parameters={"agency_id": agency_id, "cutoff": jst_midnight_utc(today)}
            ).result_rows
        ]
        archived = days_with_source(ch_client, agency_id, days, live_sourced=False)
        for day in sorted(archived):
            logger.warning("promote: agency %s %s already holds archive rows in updates; not promoted", agency_id, day)
        todo = [d for d in days if d not in archived]
        return sum(_promote_day(ch_client, agency_id, day, today) for day in todo)


def _promote_day(ch_client, agency_id: int, day: date, today: date) -> int:
    window = {"agency_id": agency_id, "lo": jst_midnight_utc(day), "hi": jst_midnight_utc(day + timedelta(days=1))}
    live = {name: n for name, n in ch_client.query(_LIVE_FILES_SQL, parameters=window).result_rows}
    done = {name: n for name, n in ch_client.query(_DONE_FILES_SQL, parameters=window).result_rows}
    short = sorted(name for name, n in live.items() if name in done and done[name] != n)
    if short:
        raise PromotionIncomplete(
            f"agency {agency_id} {day}: short in updates, fewer rows than updates_live holds: {', '.join(short)}"
        )
    new = sorted(name for name in live if name not in done)
    if not new:
        return 0
    if day < today - timedelta(days=1):
        logger.warning(
            "promote: agency %s %s promoted late; updates_live drops a day 3 days after capture", agency_id, day
        )
    params = {**window, "files": new}
    ch_client.command(_COPY_SQL, parameters=params)
    copied = ch_client.query(_COPIED_ROWS_SQL, parameters=params).result_rows[0][0]
    expected = sum(live[name] for name in new)
    if copied < expected:
        raise PromotionIncomplete(f"agency {agency_id} {day}: {copied} of {expected} live rows reached updates")
    logger.info("promote: agency %s %s: %d file(s), %d row(s)", agency_id, day, len(new), copied)
    return copied
