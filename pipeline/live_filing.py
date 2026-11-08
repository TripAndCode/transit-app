"""How a read of the observations outside analyze files its rows.

analyze files each row under the timetable in force on its service date
(pipeline.static_archive.Stamp), so a live scan does too, or the two would
give one day two figures. A read binds the versions in force over the service
dates it reaches (Filing.between). It joins the schedule archive (REFIT) only
when one of those dates holds a row whose schedule the archive replaces;
otherwise every row already carries the schedule in force, and the read takes
the rows as ingested, which files them the same way without the join. No
reader here reads a row's version name, the one column the two differ in.

A live scan answers inside the API's time budget, so a REFIT read joins the
archive on a hashed key (static_archive.observation_rows) and, under a time
band, first narrows the rows to those the band can keep (band_candidates).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from api.range import TimeBand, UpdatesFilter, time_band_clause_ch_for
from pipeline import static_archive
from pipeline.cache import async_lru_cache, clear_all
from pipeline.clickhouse import UPDATES_TABLE, checked_table
from pipeline.service_day import service_date_of, service_day_start_utc
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.strategies.static_join import VERSION_STAMPING_STRATEGIES


class Filing(NamedTuple):
    """How one read files its rows, and the parameters its SQL binds besides
    its own. *between* is the first and last service date the read may reach,
    both inclusive, or None for any: the versions in force it binds are those
    over it, and a row of a date outside it would be read as having none."""

    stamp: Stamp
    between: tuple[date, date] | None
    params: dict

    def check(self, first: date, last: date) -> None:
        """Raise unless a read of the service dates [first, last] stays within *between*."""
        if self.between is not None and not (self.between[0] <= first and last <= self.between[1]):
            raise ValueError(f"a read of {first}..{last} reaches outside its filing's {self.between}")


# Rows as ingested, for a reader of no agency's versions in force.
AS_INGESTED = Filing(Stamp.INGESTED, None, {})

# How many version names the agency's archive maps and has spans for, and the
# newest of each. A load or an archive backfill in any process moves it, so a
# join cached under it is never read after the archive changed.
_ARCHIVE_STATE_SQL = (
    f"SELECT (SELECT tuple(count(), max(archived_at)) FROM {static_archive.VERSION_MAP} "
    "WHERE agency_id = {agency:Int32}), "
    f"(SELECT tuple(count(), max(recorded_at)) FROM {static_archive.SPANS} WHERE agency_id = {{agency:Int32}})"
)


async def schedule_join(conn, ch, agency_id: int) -> ScheduleJoin | None:
    """The agency's version map with its versions in force, as analyze reads
    them (static_archive.schedule_join); None for an agency whose ingest
    stamps no version, whose rows keep the timetable they were ingested
    against."""
    if not await _stamps_versions(conn, agency_id):
        return None
    state = await _archive_state(ch, agency_id)
    # Rows a compute cache holds were filed under the archive as it stood, so
    # they are forgotten once it moves, as a recorded build forgets them
    # (pipeline.reports.definition.stamp_build): a figure is never served
    # beside a stamp naming another version than the one it was filed under.
    if _seen_states.setdefault(agency_id, state) != state:
        clear_all()
        _seen_states[agency_id] = state
    return await _schedule_join(ch, agency_id, state)


# The archive state each agency's join was last read under.
_seen_states: dict[int, tuple] = {}

# Each agency's archive state, until the monotonic time it expires: one read
# serves every live read of a request, and a state that moves in another
# process is seen within the TTL. Not a compute cache: clear_all, which
# stamp_build can run between a live report's stamp and its rows, would make
# one request read it twice.
_states: dict[int, tuple[float, tuple]] = {}
_ARCHIVE_STATE_TTL_SECONDS = 15


async def _archive_state(ch, agency_id: int) -> tuple:
    now = time.monotonic()
    cached = _states.get(agency_id)
    if cached is not None and now < cached[0]:
        return cached[1]
    state = tuple((await ch.query(_ARCHIVE_STATE_SQL, parameters={"agency": agency_id})).result_rows[0])
    _states[agency_id] = (now + _ARCHIVE_STATE_TTL_SECONDS, state)
    return state


@async_lru_cache(maxsize=256, ttl_seconds=300)
async def _stamps_versions(conn, agency_id: int) -> bool:
    """Whether the agency's ingest stamps each row's timetable version."""
    strategy = await conn.fetchval("SELECT ingest_strategy FROM agencies WHERE agency_id = $1", agency_id)
    return strategy in VERSION_STAMPING_STRATEGIES


# Cleared with every compute cache (pipeline.cache.clear_all), which a build
# analyze records reaches, and by invalidate_schedule_joins after a load; the
# TTL bounds a schedule copied in another process under names already mapped.
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def _schedule_join(ch, agency_id: int, state: tuple) -> ScheduleJoin:
    return await static_archive.schedule_join_async(ch, agency_id, None, by_service_date=True)


def invalidate_schedule_joins() -> None:
    """Forget every cached join and archive state, after a load changed an agency's timetables."""
    _schedule_join.cache_clear()  # type: ignore[attr-defined]
    _states.clear()


async def filing(
    conn,
    ch,
    agency_id: int,
    first: date,
    last: date,
    *,
    table: str = UPDATES_TABLE,
    since: datetime | None = None,
) -> Filing:
    """How a read of *table*'s rows of the service dates [first, last] files
    them. *since*, for a read of a window that starts within *first*, is the
    earliest capture it reads, so deciding costs the window, not the day."""
    between = (first, last)
    join = await schedule_join(conn, ch, agency_id)
    params = {} if join is None else join.filing_params(between)
    if join is None or not params["force_stems"]:
        return Filing(Stamp.INGESTED, between, {})
    if not refits(join, await _stamped_spans(ch, agency_id, between, table, since)):
        return Filing(Stamp.INGESTED, between, {})
    return Filing(Stamp.REFIT, between, params)


def refits(join: ScheduleJoin, stamped: Iterable[tuple[str, date, date]]) -> bool:
    """Whether Stamp.REFIT reads any row's schedule from the archive, for rows
    stamped with each version name ('' for none) of *stamped* on service dates
    from its first to its last. By static_archive's rule: a row is refiled
    when the version in force on its date has other content than the one its
    name maps to, and a name the archive does not map maps to none. Taking
    each name to have rows on every date of its span can only answer yes
    where no row needs it, which reads the same rows, joined."""
    mapped = dict(zip(join.stems, join.hashes, strict=True))
    in_force = join.in_force()
    return any(
        seg_first <= last and first <= seg_last and content != mapped.get(stem, "")
        for stem, first, last in stamped
        for seg_first, seg_last, _name, content in in_force
    )


# Each version name the agency's rows in the window were stamped with, and
# its first and last capture. It reads the rows the read it decides about
# reads, but only two of their columns, grouped by one LowCardinality column,
# so it costs a fraction of that read.
_STAMPED_SPANS_SQL = (
    "SELECT ifNull(u.static_version_id, '') AS stem, min(u.captured_at), max(u.captured_at) FROM {table} AS u "
    "WHERE u.agency_id = {{agency_id:UInt16}} AND u.captured_at >= {{stamped_from:DateTime64}} "
    "AND u.captured_at < {{stamped_to:DateTime64}} GROUP BY stem"
)


async def _stamped_spans(
    ch, agency_id: int, between: tuple[date, date], table: str, since: datetime | None = None
) -> list[tuple[str, date, date]]:
    """Each version name *table*'s rows of the service dates *between*,
    captured from *since* when given, carry, with the first and last date it is on."""
    result = await ch.query(
        _STAMPED_SPANS_SQL.format(table=checked_table(table)),
        parameters={
            "agency_id": agency_id,
            "stamped_from": since or service_day_start_utc(between[0]),
            "stamped_to": service_day_start_utc(between[1] + timedelta(days=1)),
        },
    )
    return [(stem, _service_date(first), _service_date(last)) for stem, first, last in result.result_rows]


def _service_date(moment: datetime) -> date:
    return service_date_of(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))


def band_candidates(where: UpdatesFilter, band: TimeBand, filing: Filing) -> UpdatesFilter:
    """*where*, its raw part also keeping, under REFIT, only the rows the time
    band *band* can keep once filed, so the archive join reads those alone.

    A row the archive does not refile keeps the schedule it was stored with,
    so it is in the band as stored; a refiled one takes a stop time of the
    contents in force, so its trip has one in the band. Under any other stamp,
    or with no band, *where* as it is. It binds the agency and
    ScheduleJoin.filing_params, as REFIT does.
    """
    band_sql, _params = time_band_clause_ch_for(band)
    if filing.stamp is not Stamp.REFIT or band_sql == "1":
        return where
    trips = (
        f"SELECT trip_id FROM {static_archive.SCHEDULE} AS u WHERE u.agency_id = {{agency_id:UInt16}} "
        f"AND has({{force_contents:Array(String)}}, u.content_hash) AND {band_sql}"
    )
    return where._replace(raw=f"{where.raw} AND ({band_sql} OR u.trip_id IN ({trips}))")
