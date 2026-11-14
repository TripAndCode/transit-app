"""The rows of each service date analyze reads refiled, written once.

A row is refiled (static_archive.Stamp.REFIT) where the version in force on
its date has other content than the one ingest joined, and reading it so
joins the schedule archive, which at an agency's scale costs several times
the read itself. analyze writes each such date's rows here as REFIT files
them (write), and its own reads and the live scans then take them in place
of the join (Stamp.FILED).

Written rows serve a read only while the filing they were written under
holds (valid_builds): the version and content in force on the date, the
content each version name on its rows maps to, and how many rows `updates`
files under it. Anything that moves one of those, a load, a span recorded, a
name remapped, rows landing or moving, makes a read join the archive again
until analyze writes the date anew, so nothing that moves a filing has to
clear this. Like analyze's own ledger, the count cannot see rows replaced by
as many others.
"""

import secrets
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timezone
from itertools import groupby
from typing import NamedTuple

from pipeline import ch_aggregates
from pipeline.clickhouse import UPDATE_COLUMNS
from pipeline.static_archive import FILED_COLUMNS, FILED_DATES, FILED_TABLE, ScheduleJoin, refiled_rows_sql


class Written(NamedTuple):
    """What one date's written rows were filed against."""

    build: str  # the build a read takes, '' for a date written off
    stem: str  # the version in force on the date
    content: str  # and its content
    stamped: dict[str, str]  # each version name on the date's rows ('' for none), with the content it mapped to
    rows: int  # how many rows `updates` filed under the date


_LATEST_SQL = (
    "SELECT filed_date, argMax(tuple(build, static_version_id, content_hash, stamped, source_rows), built_at) "
    f"FROM {FILED_DATES} WHERE agency_id = {{agency:UInt16}} {{dates}}GROUP BY filed_date"
)
_BETWEEN = "AND filed_date BETWEEN {first:Date} AND {last:Date} "


def _latest(between: tuple[date, date] | None) -> tuple[str, dict]:
    if between is None:
        return _LATEST_SQL.replace("{dates}", ""), {}
    return _LATEST_SQL.replace("{dates}", _BETWEEN), {"first": between[0], "last": between[1]}


def _written(rows) -> dict[date, Written]:
    return {
        day: Written(build, stem, content, dict(stamped), int(count))
        for day, (build, stem, content, stamped, count) in rows
    }


def written(ch, agency_id: int, between: tuple[date, date] | None = None) -> dict[date, Written]:
    """The agency's latest write of each date, of those *between* (both inclusive) when given."""
    sql, params = _latest(between)
    return _written(ch.query(sql, parameters={"agency": agency_id, **params}).result_rows)


async def written_async(ch, agency_id: int, between: tuple[date, date]) -> dict[date, Written]:
    """written through an async ClickHouse client."""
    sql, params = _latest(between)
    return _written((await ch.query(sql, parameters={"agency": agency_id, **params})).result_rows)


def require_tables(ch) -> None:
    """Refuse to analyze against a ClickHouse store that predates the written rows."""
    names = {
        row[0]
        for row in ch.query(
            "SELECT name FROM system.tables WHERE database = currentDatabase() AND has({names:Array(String)}, name)",
            parameters={"names": [FILED_TABLE, FILED_DATES]},
        ).result_rows
    }
    missing = [table for table in (FILED_TABLE, FILED_DATES) if table not in names]
    if missing:
        raise RuntimeError(f"ClickHouse has no {', '.join(missing)}; run `make ch-bootstrap`")


def _in_force_at(join: ScheduleJoin, day: date) -> tuple[str, str] | None:
    return next(((stem, content) for first, last, stem, content in join.in_force() if first <= day <= last), None)


def valid_builds(
    join: ScheduleJoin,
    written: dict[date, Written],
    stamped: Iterable[tuple[str, date, int]],
    dates: Iterable[date],
) -> dict[date, str] | None:
    """The build of each of *dates*, or None unless every one's written rows
    still file as REFIT would under *join*. *stamped* holds each version
    name ('' for none) on `updates`' rows of a date, the date, and its rows
    there, for every date of *dates*."""
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    names: dict[date, dict[str, int]] = defaultdict(dict)
    for stem, day, count in stamped:
        names[day][stem] = names[day].get(stem, 0) + count
    builds = {}
    for day in dates:
        kept = written.get(day)
        if kept is None or not kept.build or _in_force_at(join, day) != (kept.stem, kept.content):
            return None
        here = names.get(day, {})
        if sum(here.values()) != kept.rows or set(here) != set(kept.stamped):
            return None
        if any(mapped.get(stem, "") != content for stem, content in kept.stamped.items()):
            return None
        builds[day] = kept.build
    return builds


_COLUMNS = ", ".join([*UPDATE_COLUMNS, "filed_date", *(f"filed_{column}" for column in FILED_COLUMNS), "build"])
_STATS_SQL = (
    "SELECT filed_date, count(), groupUniqArray(ifNull(static_version_id, '')) "
    f"FROM {FILED_TABLE} WHERE agency_id = {{agency:UInt16}} AND has({{dates:Array(Date)}}, filed_date) "
    "AND startsWith(build, {build:String}) GROUP BY filed_date"
)
_RECORD_COLUMNS = [
    "agency_id",
    "filed_date",
    "build",
    "static_version_id",
    "content_hash",
    "stamped",
    "source_rows",
    "built_at",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def write(ch, agency_id: int, join: ScheduleJoin, dates: Iterable[date]) -> ScheduleJoin:
    """Write each of *dates*' rows as REFIT files them under *join*, one
    month at a time, record what each date was filed against, and return
    *join* naming them written (ScheduleJoin.filed).

    Each date is written whole under a build of its own, and recorded only
    once written, so a read never takes a date half written. Rows of a build
    no date's latest write names are dropped first: a later write's rows
    never mix with them, and a read still holding the write before is left
    its rows. Under the agency's lock, as analyze holds it."""
    days = sorted(set(dates))
    if not days:
        return join
    _drop_superseded(ch, agency_id)
    token = secrets.token_hex(8)
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    builds: dict[date, str] = {}
    records = []
    for _month, in_month in groupby(days, key=lambda day: (day.year, day.month)):
        month_days = list(in_month)
        where, params = ch_aggregates.days_where(month_days)
        rows = refiled_rows_sql(f"u.agency_id = {{agency_id:UInt16}} AND ({where})")
        ch.command(
            f"INSERT INTO {FILED_TABLE} ({_COLUMNS}) "
            f"SELECT *, concat({{build:String}}, '-', toString(filed_date)) FROM ({rows})",
            parameters={
                "agency_id": agency_id,
                "build": token,
                **params,
                **join.params(between=(month_days[0], month_days[-1])),
            },
        )
        stats = {
            day: (count, stems)
            for day, count, stems in ch.query(
                _STATS_SQL, parameters={"agency": agency_id, "dates": month_days, "build": token}
            ).result_rows
        }
        for day in month_days:
            count, stems = stats.get(day, (0, []))
            stem, content = _in_force_at(join, day) or ("", "")
            builds[day] = f"{token}-{day.isoformat()}"
            stamped = {name: mapped.get(name, "") for name in stems}
            records.append((agency_id, day, builds[day], stem, content, stamped, count, _now()))
    ch.insert(FILED_DATES, records, column_names=_RECORD_COLUMNS)
    return join._replace(filed=tuple(sorted({**dict(join.filed), **builds}.items())))


def write_off(ch, agency_id: int, dates: Iterable[date] | None, keep: Iterable[date] = ()) -> None:
    """Record that no read takes the written rows of *dates* (every date when
    None) but *keep* any more: the dates analyze finds no row of refiled on.
    Their rows go at the next write."""
    within = None if dates is None else set(dates)
    kept_dates = set(keep)
    gone: list[tuple] = [
        (agency_id, day, "", "", "", {}, 0, _now())
        for day, kept in written(ch, agency_id).items()
        if kept.build and day not in kept_dates and (within is None or day in within)
    ]
    if gone:
        ch.insert(FILED_DATES, gone, column_names=_RECORD_COLUMNS)


def _drop_superseded(ch, agency_id: int) -> None:
    live = sorted(kept.build for kept in written(ch, agency_id).values() if kept.build)
    where = "agency_id = {agency:UInt16} AND NOT has({live:Array(String)}, build)"
    parameters = {"agency": agency_id, "live": live}
    if ch.query(f"SELECT count() FROM {FILED_TABLE} WHERE {where}", parameters=parameters).result_rows[0][0]:
        ch.command(f"DELETE FROM {FILED_TABLE} WHERE {where}", parameters=parameters)
