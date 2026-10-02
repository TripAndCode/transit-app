"""Daily promotion: each closed JST day moves from `updates_live` into `updates`.

`updates` and every aggregate hold closed JST days only. A day reaches history
once it has ended, by this copy, as exactly the polls the live path collected.

Idempotent and self-repairing per file. Files are compared by distinct rows,
since two overlapping pushes of one poll can both land in `updates_live`, and
a file is copied again whenever `updates` holds fewer of its distinct rows
than `updates_live` does: only the rows `updates` lacks are inserted. An
`INSERT ... SELECT` into a MergeTree table commits in blocks, so a run that
fails partway through a file leaves some of its rows in `updates`; the next
run fills the gap rather than reading the file as done. A copy that still
falls short raises PromotionIncomplete for that day, after every other day
has been tried, so one stuck day never holds back the rest. A day has one
source: a day for which `updates` holds archive rows is not promoted (and is
logged), the mirror of archive ingest refusing a day that holds promoted rows.

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
_ROW = f"tuple({_COLUMNS})"
_LIVE_FILES_SQL = f"SELECT file_name, uniqExact({_ROW}) AS n FROM {LIVE_TABLE} " + _WINDOW + "GROUP BY file_name"
_DONE_FILES_SQL = f"SELECT file_name, uniqExact({_ROW}) AS n FROM {UPDATES_TABLE} " + _WINDOW + "GROUP BY file_name"
_IN_FILES = "AND has({files:Array(String)}, file_name) "
# transform_null_in makes NULL match NULL in the NOT IN below; most columns
# are Nullable, and without it a row holding a NULL would never count as
# already copied.
_COPY_SETTINGS = {"transform_null_in": 1}
_COPY_SQL = (
    f"INSERT INTO {UPDATES_TABLE} ({_COLUMNS}) SELECT DISTINCT {_COLUMNS} FROM {LIVE_TABLE} "
    + _WINDOW
    + _IN_FILES
    + f"AND {_ROW} NOT IN (SELECT {_ROW} FROM {UPDATES_TABLE} "
    + _WINDOW
    + _IN_FILES
    + ")"
)


class PromotionIncomplete(RuntimeError):
    """`updates` still holds fewer distinct rows than `updates_live` for some
    file of a closed day after its copy."""


def promote_closed_days(agency_id: int, conn, ch_client, *, now: datetime | None = None) -> int:
    """Copy every closed JST day `updates_live` holds for *agency_id* into
    `updates`, and return the rows copied.

    Holds the agency's `updates` lock for the whole run, as analyze() does, so
    a copy never lands between analyze's reads (pipeline/locks.py). Callers
    must also hold INGEST_ANALYZE_LOCK_KEY's single-argument ingest/analyze
    lock, as the cron sweep does around its per-agency loop, so archive
    ingest cannot interleave its own one-source-per-day check between this
    function's check and its write.
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
        copied, stuck = 0, []
        for day in (d for d in days if d not in archived):
            try:
                copied += _promote_day(ch_client, agency_id, day, today)
            except PromotionIncomplete as exc:
                stuck.append(str(exc))
        if stuck:
            raise PromotionIncomplete("; ".join(stuck))
        return copied


def _promote_day(ch_client, agency_id: int, day: date, today: date) -> int:
    window = {"agency_id": agency_id, "lo": jst_midnight_utc(day), "hi": jst_midnight_utc(day + timedelta(days=1))}
    live = dict(ch_client.query(_LIVE_FILES_SQL, parameters=window).result_rows)
    done = dict(ch_client.query(_DONE_FILES_SQL, parameters=window).result_rows)
    pending = sorted(name for name, n in live.items() if done.get(name, 0) < n)
    if not pending:
        return 0
    if day < today - timedelta(days=1):
        logger.warning(
            "promote: agency %s %s promoted late; updates_live drops a day 3 days after capture", agency_id, day
        )
    ch_client.command(_COPY_SQL, parameters={**window, "files": pending}, settings=_COPY_SETTINGS)
    after = dict(ch_client.query(_DONE_FILES_SQL, parameters=window).result_rows)
    short = [name for name in pending if after.get(name, 0) < live[name]]
    if short:
        raise PromotionIncomplete(
            f"agency {agency_id} {day}: updates holds fewer distinct rows than updates_live for {', '.join(short)}"
        )
    copied = sum(after[name] - done.get(name, 0) for name in pending)
    logger.info("promote: agency %s %s: %d file(s), %d row(s)", agency_id, day, len(pending), copied)
    return copied
