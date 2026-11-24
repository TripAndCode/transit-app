"""The stops a timetable revision left with a new gap: within a band of hours
of a day type's typical day, the longest stretch with no departure was at
most a threshold before the revision and exceeds it after. It is the list
of 交通空白 a revision opened, for the monitoring lists outside the operator.

Each side is read on the typical days the stop comparison reads
(pipeline.reports.timetable_diff), its departures in service-day seconds.
The longest gap over a band is the supply map's
(pipeline.stop_supply.band_supply), and it exceeds a threshold as it does
on that map: strictly, so a gap exactly at the threshold is not over it.

The definitions a reader sees are in docs/methodology.md (新たな運行空白).
"""

from __future__ import annotations

import asyncio
from array import array
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from pipeline import timetable_sampling
from pipeline.cache import async_lru_cache
from pipeline.reports.timetable_diff import DAY_TYPES, Revision, RevisionDayType, Sides, Unavailable, load_sides
from pipeline.stop_supply import BandSupply, band_supply, hour_cells

# day type -> stop_id -> the stop's departures on one side's typical day,
# sorted service-day seconds. An array, not a tuple of ints, because a cached
# revision holds every departure of both timetables that changed.
Departures = Mapping[RevisionDayType, Mapping[str, array]]
# day type -> stop_id -> (before, after)
Changed = Mapping[RevisionDayType, Mapping[str, tuple[array, array]]]


def departures_of(rows: Iterable[tuple]) -> dict[RevisionDayType, dict[str, array]]:
    """timetable_sampling.DEPARTURE_SECONDS_SQL's rows as Departures."""
    out: dict[RevisionDayType, dict[str, array]] = {day_type: {} for day_type in DAY_TYPES}
    for index, stop_id, secs in rows:
        out[DAY_TYPES[index - 1]][stop_id] = array("i", sorted(secs))
    return out


def changed_departures(
    before: Departures, after: Departures, known: Iterable[RevisionDayType]
) -> dict[RevisionDayType, dict[str, tuple[array, array]]]:
    """Each stop of the *known* day types, those both sides know, whose
    departures differ, with both sides'; a stop one side lacks departs
    nothing there. A stop whose departures are alike has alike gaps in every
    band, so no other can have a new one."""
    out: dict[RevisionDayType, dict[str, tuple[array, array]]] = {}
    for day_type in known:
        was, now = before.get(day_type, {}), after.get(day_type, {})
        out[day_type] = {
            stop_id: (was.get(stop_id, array("i")), now.get(stop_id, array("i")))
            for stop_id in was.keys() | now.keys()
            if was.get(stop_id) != now.get(stop_id)
        }
    return out


def _band(secs: Sequence[int], from_hour: int, to_hour: int) -> BandSupply:
    # band_supply reads only the band's hours, so the band's departures alone
    # give it the same answer without dividing the rest of the day into hours.
    lo, hi = bisect_left(secs, from_hour * 3600), bisect_left(secs, to_hour * 3600)
    return band_supply(hour_cells(secs[lo:hi]), from_hour, to_hour)


class NewGap(NamedTuple):
    """A stop whose longest gap over the band exceeds the threshold on the
    typical day after the revision and did not before. Departures are those
    within the band; gaps are seconds."""

    stop_id: str
    day_type: RevisionDayType
    departures_before: int
    departures_after: int
    gap_before: int
    gap_after: int


def new_gaps(
    changed: Changed, day_types: Iterable[RevisionDayType], from_hour: int, to_hour: int, threshold_sec: int
) -> list[NewGap]:
    """The new gaps of *day_types* over service-day hours [*from_hour*,
    *to_hour*), the longest after the revision first, then those that grew
    the most."""
    out: list[NewGap] = []
    for day_type in day_types:
        for stop_id, (was, now) in changed.get(day_type, {}).items():
            before, after = _band(was, from_hour, to_hour), _band(now, from_hour, to_hour)
            if before.longest_gap_sec <= threshold_sec < after.longest_gap_sec:
                out.append(
                    NewGap(
                        stop_id,
                        day_type,
                        before.departures,
                        after.departures,
                        before.longest_gap_sec,
                        after.longest_gap_sec,
                    )
                )
    out.sort(key=lambda g: (-g.gap_after, g.gap_before - g.gap_after, DAY_TYPES.index(g.day_type), g.stop_id))
    return out


class RevisionDepartures(NamedTuple):
    """Both sides' typical days, the day types both know, and the departures
    of each stop that changed on them (changed_departures)."""

    sides: Sides
    known: tuple[RevisionDayType, ...]
    changed: Changed


async def _departures(
    ch, agency_id: int, content: str, days: Mapping[RevisionDayType, timetable_sampling.TypicalDay | None]
) -> dict[RevisionDayType, dict[str, array]]:
    services = timetable_sampling.services_by_day_type(days)
    if not any(services):
        return departures_of([])
    parameters = {"agency": agency_id, "content": content, "services": services}
    result = await ch.query(timetable_sampling.DEPARTURE_SECONDS_SQL, parameters=parameters)
    return departures_of(result.result_rows)


# A revision names both contents and both calendar digests, which together fix
# its departures, so an entry never goes stale through a rebuild. The band and
# threshold are read from the entry, so changing either reads nothing again.
@async_lru_cache(maxsize=8, ttl_seconds=24 * 3600, agency_arg="agency_id", rebuild="never")
async def load_departures(ch, agency_id: int, revision: Revision) -> RevisionDepartures | Unavailable:
    """*revision*'s changed departures, or why it has none
    (timetable_diff.load_sides). Shared by every caller; not to be modified."""
    sides = await load_sides(ch, agency_id, revision)
    if isinstance(sides, Unavailable):
        return sides
    known = tuple(dt for dt in DAY_TYPES if sides.before.get(dt) is not None and sides.after.get(dt) is not None)
    before, after = await asyncio.gather(
        _departures(ch, agency_id, revision.before.content, sides.before),
        _departures(ch, agency_id, revision.after.content, sides.after),
    )
    return RevisionDepartures(sides, known, changed_departures(before, after, known))
