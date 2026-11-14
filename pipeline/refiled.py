"""The rows of each service date analyze reads refiled, written once.

A row is refiled (static_archive.Stamp.REFIT) where the version in force on
its date has other content than the one ingest joined, and reading it so
joins the schedule archive, which at an agency's scale costs several times
the read itself. analyze writes each such date's rows here as REFIT files
them (write), and its own reads and the live scans then take them in place
of the join (Stamp.FILED).

Written rows serve a read only while the filing they were written under
holds (valid_builds): the filing rules (RULES), the version and content in
force on the date, the content each version name on its rows maps to, and
how many rows `updates` files under it. A load, a span recorded, a name
remapped or rows landing moves one of those, and a read then joins the
archive again until analyze writes the date anew. Like analyze's own ledger,
the count cannot see rows replaced by as many others, so whatever rewrites
`updates` rows in place writes their agencies' dates off first
(rows_rewritten).
"""

import hashlib
import secrets
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from itertools import groupby
from typing import NamedTuple

from pipeline import ch_aggregates
from pipeline.clickhouse import UPDATE_COLUMNS, UPDATES_TABLE
from pipeline.static_archive import (
    FILED_COLUMNS,
    FILED_DATE_SQL,
    FILED_DATES,
    FILED_TABLE,
    ScheduleJoin,
    refiled_rows_sql,
)

# The filing rules a write follows: its own SQL, which carries REFIT's. Rows
# written under other rules serve no read.
RULES = hashlib.sha256(refiled_rows_sql("{where}").encode()).hexdigest()[:16]


class Written(NamedTuple):
    """What one date's written rows were filed against."""

    build: str  # the build a read takes, '' for a date written off
    stem: str  # the version in force on the date
    content: str  # and its content
    stamped: dict[str, str]  # each version name on the date's rows ('' for none), with the content it mapped to
    rows: int  # how many rows `updates` filed under the date
    rules: str  # the RULES they were written under


_LATEST_SQL = (
    "SELECT filed_date, argMax(tuple(build, static_version_id, content_hash, stamped, source_rows, rules), built_at) "
    f"FROM {FILED_DATES} WHERE agency_id = {{agency:UInt16}} {{dates}}GROUP BY filed_date"
)
_BETWEEN = "AND filed_date BETWEEN {first:Date} AND {last:Date} "


def _latest(between: tuple[date, date] | None) -> tuple[str, dict]:
    if between is None:
        return _LATEST_SQL.replace("{dates}", ""), {}
    return _LATEST_SQL.replace("{dates}", _BETWEEN), {"first": between[0], "last": between[1]}


def _written(rows) -> dict[date, Written]:
    return {
        day: Written(build, stem, content, dict(stamped), int(count), rules)
        for day, (build, stem, content, stamped, count, rules) in rows
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
    still file as REFIT would under *join* and RULES. *stamped* holds each
    version name ('' for none) on `updates`' rows of a date, the date, and
    its rows there, for every date of *dates*."""
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    names: dict[date, dict[str, int]] = defaultdict(dict)
    for stem, day, count in stamped:
        names[day][stem] = names[day].get(stem, 0) + count
    builds = {}
    for day in dates:
        kept = written.get(day)
        if kept is None or not kept.build or kept.rules != RULES:
            return None
        if _in_force_at(join, day) != (kept.stem, kept.content):
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
    f"FROM {FILED_TABLE} WHERE agency_id = {{agency:UInt16}} "
    "AND filed_date BETWEEN {first:Date} AND {last:Date} AND filed_date IN {dates:Array(Date)} "
    "AND build IN {builds:Array(String)} GROUP BY filed_date"
)
_RECORD_COLUMNS = [
    "agency_id",
    "filed_date",
    "build",
    "static_version_id",
    "content_hash",
    "stamped",
    "source_rows",
    "rules",
]
_STAMPED_DAYS_SQL = (
    f"SELECT ifNull(u.static_version_id, '') AS stem, {FILED_DATE_SQL} AS filed, count() FROM {UPDATES_TABLE} AS u "
    "WHERE u.agency_id = {agency_id:UInt16} AND ({where}) GROUP BY stem, filed"
)


def stamped_days(ch, agency_id: int, dates: Iterable[date]) -> list[tuple[str, date, int]]:
    """Each version name ('' for none) on `updates`' rows filed under one of
    *dates*, the date, and its rows there, as valid_builds reads them."""
    days = sorted(set(dates))
    if not days:
        return []
    where, params = ch_aggregates.days_where(days)
    rows = ch.query(
        _STAMPED_DAYS_SQL.replace("{where}", where), parameters={"agency_id": agency_id, **params}
    ).result_rows
    return [(stem, day, int(count)) for stem, day, count in rows]


def reusable(ch, agency_id: int, join: ScheduleJoin, dates: Iterable[date]) -> dict[date, str]:
    """The build of each of *dates* whose latest write still serves a read under *join* (valid_builds)."""
    days = sorted(set(dates))
    if not days:
        return {}
    kept, stamped = written(ch, agency_id, (days[0], days[-1])), stamped_days(ch, agency_id, days)
    return {day: builds[day] for day in days if (builds := valid_builds(join, kept, stamped, [day])) is not None}


def refresh(ch, agency_id: int, join: ScheduleJoin, dates: Iterable[date]) -> ScheduleJoin:
    """*join* naming each of *dates* written (ScheduleJoin.filed): by its
    latest write where that still serves a read (reusable), else by a new
    one (write)."""
    days = sorted(set(dates))
    kept = reusable(ch, agency_id, join, days)
    join = join._replace(filed=tuple(sorted({**dict(join.filed), **kept}.items())))
    return write(ch, agency_id, join, [day for day in days if day not in kept])


def write(ch, agency_id: int, join: ScheduleJoin, dates: Iterable[date]) -> ScheduleJoin:
    """Write each of *dates*' rows as REFIT files them under *join*, one
    month at a time, record what each date was filed against, and return
    *join* naming them written (ScheduleJoin.filed).

    Each date is written whole under a build of its own, and recorded only
    once written, so a read never takes a date half written. Rows of a build
    no date's latest write names are dropped first, whether or not anything
    is written: a later write's rows never mix with them, and a read still
    holding the write before is left its rows until the next call. Under the
    agency's lock, as analyze holds it."""
    days = sorted(set(dates))
    _drop_superseded(ch, agency_id)
    if not days:
        return join
    token = secrets.token_hex(8)
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    builds: dict[date, str] = {}
    for _month, in_month in groupby(days, key=lambda day: (day.year, day.month)):
        month_days = list(in_month)
        month_builds = {day: f"{token}-{day.isoformat()}" for day in month_days}
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
        parameters = {
            "agency": agency_id,
            "first": month_days[0],
            "last": month_days[-1],
            "dates": month_days,
            "builds": list(month_builds.values()),
        }
        stats = {day: (count, stems) for day, count, stems in ch.query(_STATS_SQL, parameters=parameters).result_rows}
        records = []
        for day in month_days:
            count, stems = stats.get(day, (0, []))
            stem, content = _in_force_at(join, day) or ("", "")
            stamped = {name: mapped.get(name, "") for name in stems}
            records.append((agency_id, day, month_builds[day], stem, content, stamped, count, RULES))
        # Each month as soon as it is written: a write that dies later keeps it.
        ch.insert(FILED_DATES, records, column_names=_RECORD_COLUMNS)
        builds.update(month_builds)
    return join._replace(filed=tuple(sorted({**dict(join.filed), **builds}.items())))


def write_off(ch, agency_id: int, dates: Iterable[date] | None, keep: Iterable[date] = ()) -> None:
    """Record that no read takes the written rows of *dates* (every date when
    None), but *keep*, any more. Their rows go at the next write."""
    within = None if dates is None else set(dates)
    kept_dates = set(keep)
    between = None if not within else (min(within), max(within))
    if within is not None and not within:
        return
    gone: list[tuple] = [
        (agency_id, day, "", "", "", {}, 0, "")
        for day, kept in written(ch, agency_id, between).items()
        if kept.build and day not in kept_dates and (within is None or day in within)
    ]
    if gone:
        ch.insert(FILED_DATES, gone, column_names=_RECORD_COLUMNS)


def rows_rewritten(ch, agency_ids: Iterable[int] | None) -> None:
    """Write off every date of *agency_ids* (every agency when None), before
    `updates` rows of theirs are rewritten in place: such a rewrite can
    leave a date's rows and version names as many as before, which
    valid_builds would not see. Their next full analyze writes them again."""
    if agency_ids is None:
        rows = ch.query(f"SELECT DISTINCT agency_id FROM {FILED_DATES}").result_rows
        agency_ids = [row[0] for row in rows]
    for agency_id in agency_ids:
        write_off(ch, agency_id, None)


def _drop_superseded(ch, agency_id: int) -> None:
    live = sorted(kept.build for kept in written(ch, agency_id).values() if kept.build)
    where = "agency_id = {agency:UInt16} AND build NOT IN {live:Array(String)}"
    parameters = {"agency": agency_id, "live": live}
    if ch.query(f"SELECT count() FROM {FILED_TABLE} WHERE {where}", parameters=parameters).result_rows[0][0]:
        ch.command(f"DELETE FROM {FILED_TABLE} WHERE {where}", parameters=parameters)
