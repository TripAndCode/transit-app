"""An archive day replaces the day promotion stored from the live path.

`updates` holds each closed JST day from one source. Promotion
(pipeline/promote.py) stores a day the live path collected as soon as it
closes, but the collector pushes only some of its polls, so that copy is a
fraction of the day its archive keeps. Archive ingest (pipeline.ingest.ingest)
therefore replaces a promoted day once it reads the day's complete archive.

Complete is what ingest can know from its own folder and run:
- The collector tars a UTC day only after that day ends, so a tarball holds
  every poll of its UTC day. JST day D runs from 09:00 of UTC day D-1 to 09:00
  of UTC day D, so it is covered once the folder holds both D-1's and D's
  tarballs (covered_days). Loose `.pb` files never cover a day: nothing says
  a folder of them is whole.
- No file that could hold rows of D failed to read or insert in the run.
- The archive holds at least as many polls of D as the collector pushed. It
  archives every poll it pushes, so fewer means a gap in the archive.
  `ingest_live`'s own samples are not counted: the archive never holds them.

The archive's rows are inserted first, through ingest's own batches, and only
then are the live-sourced rows of exactly that agency and JST day deleted
(settle), so the day never lacks both sources. Partition replacement could
swap them atomically, but `updates` is partitioned by month. A day whose
archive turns out incomplete has the archive rows the run stored deleted
instead, back to its live copy, and a later run reads them again.

Ingest holds the agency's `updates` lock (pipeline.locks.agency_ingest_lock)
from the first insert to the last delete, so analyze never reads a day holding
both sources. A run that dies between the two leaves such a day; the next
ingest of the same two tarballs finishes it, since its members are already in
the skip-list and settle reads the day as stored.

A day is never replaced when any service date its rows can be filed under lies
before the agency's retention floor (pipeline.retention.retention_floor):
those dates are final.
"""

from __future__ import annotations

import logging
import pathlib
import re
from datetime import date, datetime, timedelta
from typing import NamedTuple

from pipeline import refiled
from pipeline.clickhouse import COLLECTOR_PUSHED, LIVE_SOURCED, UPDATES_TABLE, days_with_source, jst_midnight_utc
from pipeline.service_day import filed_dates_reached

logger = logging.getLogger(__name__)

_DAY = timedelta(days=1)
_STEM_DAY = re.compile(r"(\d{8})")

_WINDOW = "agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64}"
_SOURCES_SQL = (
    f"SELECT countIf(NOT {LIVE_SOURCED}), uniqExactIf(file_name, NOT {LIVE_SOURCED}), "
    f"countIf({LIVE_SOURCED}), uniqExactIf(file_name, {COLLECTOR_PUSHED}) FROM {UPDATES_TABLE} WHERE {_WINDOW}"
)
# Waits for the delete to apply, so the recount after it reads the result.
_DELETE_SETTINGS = {"lightweight_deletes_sync": 2}


class ReplacementIncomplete(RuntimeError):
    """A delete settle issued left rows of the source it removed."""


class DaySources(NamedTuple):
    archive_rows: int
    archive_polls: int
    live_rows: int
    pushed_polls: int


def tarball_days(tarballs: list[pathlib.Path]) -> set[date]:
    """The UTC days the folder's tarballs are named for."""
    days = set()
    for tgz in tarballs:
        m = _STEM_DAY.search(tgz.name)
        if m is None:
            continue
        try:
            days.add(datetime.strptime(m.group(1), "%Y%m%d").date())
        except ValueError:
            continue
    return days


def covered_days(tarball_utc_days: set[date], today: date) -> set[date]:
    """The closed JST days both of whose tarballs are among *tarball_utc_days*."""
    return {day + _DAY for day in tarball_utc_days if day + _DAY in tarball_utc_days and day + _DAY < today}


def _window(day: date) -> tuple[datetime, datetime]:
    return jst_midnight_utc(day), jst_midnight_utc(day + _DAY)


def filed_dates(day: date) -> tuple[date, date]:
    """The first and last service date a row captured on JST *day* can be filed under."""
    return filed_dates_reached(*_window(day))


def above_floor(day: date, floor: date | None) -> bool:
    """Whether every service date JST *day*'s rows can be filed under is at or after *floor*."""
    return floor is None or filed_dates(day)[0] >= floor


def candidates(ch, agency_id: int, covered: set[date], floor: date | None) -> set[date]:
    """The days among *covered* that hold live-sourced rows and may be replaced."""
    live = days_with_source(ch, agency_id, covered, live_sourced=True)
    final = {day for day in live if not above_floor(day, floor)}
    for day in sorted(final):
        logger.warning(f"  {day} holds promoted live rows but is final (retention floor {floor}); it stays live")
    return live - final


def day_sources(ch, agency_id: int, day: date) -> DaySources:
    lo, hi = _window(day)
    row = ch.query(_SOURCES_SQL, parameters={"agency_id": agency_id, "lo": lo, "hi": hi}).result_rows[0]
    return DaySources(*(int(n) for n in row))


def _delete(ch, agency_id: int, day: date, *, live: bool) -> None:
    predicate = LIVE_SOURCED if live else f"NOT {LIVE_SOURCED}"
    lo, hi = _window(day)
    # The day's rows can change for as many others, which refiled's row count cannot see.
    first, last = filed_dates(day)
    refiled.rows_rewritten(ch, [agency_id], [first + _DAY * i for i in range((last - first).days + 1)])
    ch.command(
        f"DELETE FROM {UPDATES_TABLE} WHERE {_WINDOW} AND {predicate}",
        parameters={"agency_id": agency_id, "lo": lo, "hi": hi},
        settings=_DELETE_SETTINGS,
    )
    after = day_sources(ch, agency_id, day)
    if after.live_rows if live else after.archive_rows:
        source = "live" if live else "archive"
        raise ReplacementIncomplete(f"agency {agency_id} {day}: {source} rows remain after their delete")


def settle(ch, agency_id: int, day: date, *, intact: bool) -> str:
    """Leave JST *day* with one source: the archive when its copy is complete
    (*intact*: no file that could hold its rows failed in this run), else the
    live copy. Returns what the day now holds: ``archive`` or ``live``."""
    held = day_sources(ch, agency_id, day)
    if not held.live_rows:
        return "archive"
    if intact and held.archive_rows and held.archive_polls >= held.pushed_polls:
        _delete(ch, agency_id, day, live=True)
        logger.info(
            f"  {day}: archive ({held.archive_polls} polls, {held.archive_rows} rows) replaced "
            f"the promoted live copy ({held.live_rows} rows, {held.pushed_polls} pushed polls)"
        )
        return "archive"
    if held.archive_rows:
        _delete(ch, agency_id, day, live=False)
    reason = (
        "a file of it failed"
        if not intact
        else f"its archive holds {held.archive_polls} polls to the collector's {held.pushed_polls} pushed"
    )
    logger.warning(f"  {day}: not replaced, {reason}; it keeps its promoted live copy")
    return "live"
