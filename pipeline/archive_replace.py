"""An archive day replaces the day promotion stored from the live path.

`updates` holds each closed JST day from one source. Promotion
(pipeline/promote.py) stores a day the live path collected as soon as it
closes, but the collector pushes only some of its polls, so that copy is a
fraction of the day its archive keeps. Archive ingest (pipeline.ingest.ingest)
therefore replaces a promoted day once it reads the day's complete archive.

Complete means all of these:
- The folder covers the day. The collector tars a UTC day only after that day
  ends, so a tarball holds every poll of its UTC day. JST day D runs from 09:00
  of UTC day D-1 to 09:00 of UTC day D, so it is covered once the folder holds
  both D-1's and D's tarballs (covered_days). Loose `.pb` files never cover a
  day: nothing says a folder of them is whole.
- No file that could hold rows of D failed to read or insert in the run.
- Every poll the collector pushed on D (`oracle/<utc day>/<member>`) has its
  archive member (`<utc day>/<member>`) among D's archive rows. The collector
  archives every poll it pushes, so a missing one is a gap in the archive. The
  archive side is read _MEMBER_REACH wider than the day, since a member is
  stamped by its feed header and its push by the time the push was sent, which
  can fall either side of midnight. `ingest_live`'s own samples are not
  checked: the archive never holds them.

The archive's rows are inserted first, through ingest's own batches, and only
then are the live-sourced rows of exactly that agency and JST day deleted
(settle), so the day never lacks both sources. Partition replacement could
swap them atomically, but `updates` is partitioned by month. A day whose
archive turns out incomplete has its archive rows deleted instead, back to its
live copy, and a later run over both of its tarballs reads them again.

Ingest holds the agency's `updates` lock (pipeline.locks.agency_ingest_lock)
from the first insert to the last delete, so analyze never reads a day holding
both sources while a run is under way. A run that dies between the two leaves
such a day. Every later ingest settles each day holding both sources within
_RESIDUE_DAYS of its folder's span, judged from what `updates` holds, so the
next run of the rolling daily pair finishes it. `gtfs_pipeline.py check_aggs`
fails while any day holds both, since analyze would count it twice.

A day is never replaced when any service date its rows can be filed under lies
before the agency's retention floor (pipeline.retention.retention_floor):
those dates are final.
"""

from __future__ import annotations

import logging
import pathlib
import re
from collections.abc import Collection, Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import NamedTuple

from pipeline import refiled
from pipeline.clickhouse import COLLECTOR_PUSHED, LIVE_SOURCED, UPDATES_TABLE, days_with_source, jst_midnight_utc
from pipeline.service_day import filed_dates_reached, service_date_ch, service_day_start_utc

logger = logging.getLogger(__name__)

_DAY = timedelta(days=1)
_STEM_DAY = re.compile(r"(\d{8})")
# How far a member's header stamp and its push's stamp can lie apart.
_MEMBER_REACH = timedelta(hours=1)
# How far before a folder's span an interrupted replacement is looked for.
_RESIDUE_DAYS = 3

_WINDOW = "agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64}"
_SOURCES_SQL = (
    f"SELECT countIf(NOT {LIVE_SOURCED}), uniqExactIf(file_name, NOT {LIVE_SOURCED}), "
    f"countIf({LIVE_SOURCED}), uniqExactIf(file_name, {COLLECTOR_PUSHED}) FROM {UPDATES_TABLE} WHERE {_WINDOW}"
)
# 'oracle/' is seven characters, so the member name starts at the eighth.
_UNARCHIVED_PUSHES_SQL = (
    f"SELECT count() FROM (SELECT DISTINCT substring(file_name, 8) AS member FROM {UPDATES_TABLE} "
    f"WHERE {_WINDOW} AND {COLLECTOR_PUSHED}) WHERE member NOT IN (SELECT DISTINCT file_name FROM {UPDATES_TABLE} "
    "WHERE agency_id = {agency_id:UInt16} AND captured_at >= {wide_lo:DateTime64} "
    f"AND captured_at < {{wide_hi:DateTime64}} AND NOT {LIVE_SOURCED})"
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


def candidates(ch, agency_id: int, covered: set[date], floor: date | None) -> set[date]:
    """The days among *covered* that hold live-sourced rows and may be replaced."""
    live = days_with_source(ch, agency_id, covered, live_sourced=True)
    final = {day for day in live if not above_floor(day, floor)}
    for day in sorted(final):
        logger.warning(f"  {day} holds promoted live rows but is final (retention floor {floor}); it stays live")
    return live - final


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


def unarchived_pushes(ch, agency_id: int, day: date) -> int:
    """How many polls the collector pushed on JST *day* have no archive member stored."""
    lo, hi = _window(day)
    params = {"agency_id": agency_id, "lo": lo, "hi": hi, "wide_lo": lo - _MEMBER_REACH, "wide_hi": hi + _MEMBER_REACH}
    return int(ch.query(_UNARCHIVED_PUSHES_SQL, parameters=params).result_rows[0][0])


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


def settle(ch, agency_id: int, day: date, *, intact: bool) -> int:
    """Leave JST *day* with one source: the archive when its copy is complete
    (*intact*: no file that could hold its rows failed in this run), else the
    live copy. Returns how many archive rows it deleted to fall back to live."""
    held = day_sources(ch, agency_id, day)
    if not held.live_rows:
        return 0
    missing = unarchived_pushes(ch, agency_id, day) if intact and held.archive_rows else None
    figures = (
        f"archive {held.archive_polls} polls, {held.archive_rows} rows; "
        f"live {held.pushed_polls} pushed polls, {held.live_rows} rows"
    )
    if missing == 0:
        _delete(ch, agency_id, day, live=True)
        logger.info(f"  {day}: the archive replaced the promoted live copy ({figures})")
        return 0
    if held.archive_rows:
        _delete(ch, agency_id, day, live=False)
    reason = "a file of it failed" if not intact else f"its archive lacks {missing or 'all'} pushed poll(s)"
    logger.warning(f"  {day}: not replaced, {reason}; it keeps its promoted live copy ({figures})")
    return held.archive_rows


def settle_all(ch, agency_id: int, days: Iterable[date], failed: Collection[date]) -> tuple[int, list[str]]:
    """settle() each of *days*, each on its own so one failure leaves the rest
    settled. Returns the archive rows rolled back and each failure."""
    rolled_back, failures = 0, []
    for day in sorted(set(days)):
        try:
            rolled_back += settle(ch, agency_id, day, intact=day not in failed)
        except Exception as exc:
            logger.exception(f"  {day}: could not be settled")
            failures.append(f"{day}: {type(exc).__name__}: {exc}")
    return rolled_back, failures


def _service_date_runs(days: list[date]) -> tuple[str, dict]:
    runs: list[list[date]] = []
    for day in days:
        if runs and day - runs[-1][-1] == _DAY:
            runs[-1].append(day)
        else:
            runs.append([day])
    clauses, params = [], {}
    for i, run in enumerate(runs):
        clauses.append(f"(captured_at >= {{lo_{i}:DateTime64}} AND captured_at < {{hi_{i}:DateTime64}})")
        params[f"lo_{i}"], params[f"hi_{i}"] = service_day_start_utc(run[0]), service_day_start_utc(run[-1] + _DAY)
    return " OR ".join(clauses), params


def live_rows_moved(ch, agency_id: int, stored_live: Mapping[date, int | None]) -> set[date]:
    """The service dates among *stored_live* (agg_feed_health.live_rows, by
    capture service date) for which `updates` now holds another number of
    live-sourced rows: their source changed since they were built. Only dates stored with
    live rows are read, since a replacement only ever moves a day from live
    to archive; a NULL predates the column and is taken to match."""
    dates = sorted(day for day, live in stored_live.items() if live)
    if not dates:
        return set()
    where, params = _service_date_runs(dates)
    rows = ch.query(
        f"SELECT {service_date_ch()} AS date, countIf({LIVE_SOURCED}) FROM {UPDATES_TABLE} "
        f"WHERE agency_id = {{agency_id:UInt16}} AND ({where}) GROUP BY date",
        parameters={"agency_id": agency_id, **params},
    ).result_rows
    held = {day: int(n) for day, n in rows}
    return {day for day in dates if held.get(day, 0) != stored_live[day]}
