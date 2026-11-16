"""An archive day replaces the day promotion stored from the live path.

`updates` holds each closed JST day from one source. Promotion
(pipeline/promote.py) stores a day the live path collected as soon as it
closes, but the collector pushes only some of its polls, so that copy is a
fraction of the day its archive keeps. Archive ingest (pipeline.ingest.ingest)
therefore replaces a promoted day, but only in the run that read the day's
complete archive itself. Any other evidence keeps the live copy.

Complete means all of these, judged by that run:
- Its folder covers the day. The collector tars a UTC day only after that day
  ends, so a tarball holds every poll of its UTC day. JST day D runs from 09:00
  of UTC day D-1 to 09:00 of UTC day D, so it is covered once the folder holds
  both D-1's and D's tarballs (covered_days). Loose `.pb` files never cover a
  day: nothing says a folder of them is whole.
- No file that could hold rows of D failed to read or insert in the run.
- The collector pushed at least one poll on D, and every poll it pushed
  (`oracle/<utc day>/<member>`) has its member (`<utc day>/<member>`) in the
  tarballs the run read, or stored. The collector archives every poll it
  pushes, so a missing one is a gap in the archive. A day with no push has
  nothing to check the archive against: the live path's own samples
  (`ingest_live`) are polls the collector may have missed, so such a day keeps
  them. The stored side is read _MEMBER_REACH either side of the day, the
  furthest archive_captured_at stamps a member from its name.

A poll whose member is stamped on a neighbouring day that keeps its live copy
is refused there, while its push is deleted with the replaced day's live copy:
neither copy then holds it. That costs at most the polls straddling the
boundary of a replaced day, and is accepted, since keeping the push would
leave a day with both sources.

The archive's rows are inserted first, through ingest's own batches, and only
then are the live-sourced rows of exactly that agency and JST day deleted
(settle), so the day never lacks both sources. Partition replacement could
swap them atomically, but `updates` is partitioned by month. A day whose
archive turns out incomplete has its archive rows deleted instead, back to its
live copy, and a later run over both of its tarballs reads them again.

Ingest holds the agency's `updates` lock (pipeline.locks.agency_ingest_lock)
from the first insert to the last delete, so analyze never reads a day holding
both sources while a run is under way. A run that dies between the two leaves
such a day, which only the run that died had the evidence to replace. Every
later ingest therefore returns each day holding both sources within
_RESIDUE_DAYS of its folder's span to its live copy, unless its own folder
covers the day, and `gtfs_pipeline.py check_aggs` warns of any it finds.

A day is never replaced when any service date its rows can be filed under lies
before the agency's retention floor (pipeline.retention.retention_floor):
those dates are final.
"""

from __future__ import annotations

import logging
import pathlib
import re
from collections.abc import Collection, Iterable
from datetime import date, datetime, timedelta
from typing import NamedTuple

from pipeline import refiled
from pipeline.clickhouse import COLLECTOR_PUSHED, LIVE_SOURCED, UPDATES_TABLE, days_with_source, jst_midnight_utc
from pipeline.service_day import filed_dates_reached

logger = logging.getLogger(__name__)

_DAY = timedelta(days=1)
_STEM_DAY = re.compile(r"(\d{8})")
# archive_captured_at keeps a header on its name's JST day or the next, and
# reads a name with no usable header as JST, nine hours before its UTC time.
_MEMBER_REACH = timedelta(days=2)
# How far before a folder's span an interrupted replacement is looked for.
_RESIDUE_DAYS = 3

_WINDOW = "agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64}"
_SOURCES_SQL = (
    f"SELECT countIf(NOT {LIVE_SOURCED}), uniqExactIf(file_name, NOT {LIVE_SOURCED}), "
    f"countIf({LIVE_SOURCED}), uniqExactIf(file_name, {COLLECTOR_PUSHED}) FROM {UPDATES_TABLE} WHERE {_WINDOW}"
)
# 'oracle/' is seven characters, so the member name starts at the eighth.
_PUSHED_MEMBERS_SQL = (
    f"SELECT DISTINCT substring(file_name, 8) FROM {UPDATES_TABLE} WHERE {_WINDOW} AND {COLLECTOR_PUSHED}"
)
_STORED_MEMBERS_SQL = (
    f"SELECT DISTINCT file_name FROM {UPDATES_TABLE} WHERE {_WINDOW} AND NOT {LIVE_SOURCED} "
    "AND has({names:Array(String)}, file_name)"
)
# Waits for the delete to apply, so the recount after it reads the result.
_DELETE_SETTINGS = {"lightweight_deletes_sync": 2}


class ReplacementIncomplete(RuntimeError):
    """A day settle could not leave with one source."""


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


def _pushed_days(ch, agency_id: int, days: set[date]) -> set[date]:
    if not days:
        return set()
    rows = ch.query(
        f"SELECT DISTINCT toDate(captured_at, 'Asia/Tokyo') FROM {UPDATES_TABLE} "
        "WHERE agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64} "
        f"AND {COLLECTOR_PUSHED} AND has({{days:Array(Date)}}, toDate(captured_at, 'Asia/Tokyo'))",
        parameters={
            "agency_id": agency_id,
            "lo": jst_midnight_utc(min(days)),
            "hi": jst_midnight_utc(max(days) + _DAY),
            "days": sorted(days),
        },
    ).result_rows
    return {row[0] for row in rows}


def candidates(ch, agency_id: int, covered: set[date], floor: date | None) -> set[date]:
    """The days among *covered* that hold live-sourced rows and may be
    replaced: above the floor, and with a push to check the archive against
    (settle), so a day that could never be replaced is refused before any
    of its archive rows go in."""
    live = days_with_source(ch, agency_id, covered, live_sourced=True)
    final = {day for day in live if not above_floor(day, floor)}
    for day in sorted(final):
        logger.warning(f"  {day} holds promoted live rows but is final (retention floor {floor}); it stays live")
    unpushed = live - final - _pushed_days(ch, agency_id, live - final)
    for day in sorted(unpushed):
        logger.warning(f"  {day} holds live rows but no collector push to check its archive against; it stays live")
    return live - final - unpushed


def residue(ch, agency_id: int, span: Collection[date], floor: date | None, today: date) -> set[date]:
    """The closed days from _RESIDUE_DAYS before *span* through its end that
    hold both sources: a replacement a run left unfinished."""
    if not span:
        return set()
    first, last = min(span) - _DAY * _RESIDUE_DAYS, min(max(span), today - _DAY)
    days = [first + _DAY * i for i in range((last - first).days + 1)]
    both = days_with_source(ch, agency_id, days, live_sourced=True) & days_with_source(
        ch, agency_id, days, live_sourced=False
    )
    final = {day for day in both if not above_floor(day, floor)}
    for day in sorted(final):
        logger.error(f"  {day} holds both sources but is final (retention floor {floor}); it is left as it is")
    return both - final


def day_sources(ch, agency_id: int, day: date) -> DaySources:
    lo, hi = _window(day)
    row = ch.query(_SOURCES_SQL, parameters={"agency_id": agency_id, "lo": lo, "hi": hi}).result_rows[0]
    return DaySources(*(int(n) for n in row))


def unarchived_pushes(ch, agency_id: int, day: date, read: Collection[str]) -> int:
    """How many polls the collector pushed on JST *day* have their member
    neither among *read*, the member names the run read, nor stored."""
    lo, hi = _window(day)
    pushed = {
        row[0]
        for row in ch.query(_PUSHED_MEMBERS_SQL, parameters={"agency_id": agency_id, "lo": lo, "hi": hi}).result_rows
    }
    unread = sorted(pushed - set(read))
    if not unread:
        return 0
    params = {"agency_id": agency_id, "lo": lo - _MEMBER_REACH, "hi": hi + _MEMBER_REACH, "names": unread}
    stored = {row[0] for row in ch.query(_STORED_MEMBERS_SQL, parameters=params).result_rows}
    return len(set(unread) - stored)


def _delete(ch, agency_id: int, day: date, *, live: bool) -> None:
    predicate = LIVE_SOURCED if live else f"NOT {LIVE_SOURCED}"
    lo, hi = _window(day)
    if live:
        # The day's rows can change for as many others, which refiled's row
        # count cannot see. Taking the archive back restores what was built.
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


def _figures(held: DaySources) -> str:
    return (
        f"archive {held.archive_polls} polls, {held.archive_rows} rows; "
        f"live {held.pushed_polls} pushed polls, {held.live_rows} rows"
    )


def keep_live(ch, agency_id: int, day: date, reason: str) -> int:
    """Return JST *day* to its live copy. Returns the archive rows deleted."""
    held = day_sources(ch, agency_id, day)
    if not held.live_rows:
        return 0
    if held.archive_rows:
        _delete(ch, agency_id, day, live=False)
    logger.warning(f"  {day}: not replaced, {reason}; it keeps its promoted live copy ({_figures(held)})")
    return held.archive_rows


def settle(ch, agency_id: int, day: date, *, read: Collection[str], intact: bool) -> int:
    """Leave JST *day*, which this run's folder covers, with one source: the
    archive when its copy is complete (*intact*: no file that could hold its
    rows failed; every push among *read* or stored), else the live copy.
    Returns the archive rows deleted to fall back to live."""
    if not intact:
        return keep_live(ch, agency_id, day, "a file of it failed")
    held = day_sources(ch, agency_id, day)
    if not held.live_rows:
        return 0
    if not held.pushed_polls:
        return keep_live(ch, agency_id, day, "the collector pushed none of its polls to check the archive against")
    missing = unarchived_pushes(ch, agency_id, day, read)
    if missing:
        return keep_live(ch, agency_id, day, f"its archive lacks {missing} pushed poll(s)")
    _delete(ch, agency_id, day, live=True)
    logger.info(f"  {day}: the archive replaced the promoted live copy ({_figures(held)})")
    return 0


def settle_all(
    ch,
    agency_id: int,
    covered: Iterable[date],
    unfinished: Iterable[date],
    *,
    read: Collection[str],
    failed: Collection[date],
) -> tuple[int, list[str]]:
    """Settle each of *covered* (settle) and return each of *unfinished* to
    its live copy, each day on its own so one failure leaves the rest settled.
    Returns the archive rows deleted and each failure."""
    rolled_back, failures = 0, []
    plans = [(day, True) for day in covered] + [(day, False) for day in unfinished]
    for day, is_covered in sorted(plans):
        try:
            if is_covered:
                rolled_back += settle(ch, agency_id, day, read=read, intact=day not in failed)
            else:
                rolled_back += keep_live(ch, agency_id, day, "only the run that read its tarballs may replace it")
        except Exception as exc:
            logger.exception(f"  {day}: could not be settled")
            failures.append(f"{day}: {type(exc).__name__}: {exc}")
    return rolled_back, failures
