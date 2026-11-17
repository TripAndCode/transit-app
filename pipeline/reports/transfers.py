"""Transfer-connection reliability at a hub: for a defined pair (riders leaving
route A at its hub stops, boarding route B at its own), how often the planned
connection held given each day's delays, how long riders actually waited, and
the planned buffer that would have held 95% of connections.

The planned side is the loaded timetable's (pipeline.planned_trips): each
service day it covers, A's scheduled arrivals and B's scheduled departures at
the pair's stops, in seconds of the service day, so a stop after midnight
(24:20) stays on the day its trip runs. A day either agency's loaded calendar
does not cover has no plan and is listed apart, never counted as zero.

The observed side is each stop visit's latest feed prediction
(pipeline.db.build_stop_visits_ch_sql), filed under its trip's service day
(pipeline.service_day.filed_date_ch) and joined to the plan on (service day,
trip, stop sequence). Rows are read as ingest stamped them (Stamp.INGESTED),
not refiled under the version in force (pipeline.live_filing.filing) as the
delay reports read them: refiling rewrites a row's schedule and route, never
its delay, and the plan this joins to is the loaded timetable's, so a row
refiled under another version would be judged against two timetables at once.
A's arrival reads the visit's arrival delay where the agency's feed is
confirmed to send arrivals, else its departure delay; a bus leaves no earlier
than it arrives, so the fallback can only make A look later and a connection
look less safe. B reads the departure delay only.

The definitions a reader sees are in docs/methodology.md (乗継ぎ拠点での接続).
"""

from __future__ import annotations

import asyncio
import json
import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, NamedTuple

from clickhouse_connect.driver.external import ExternalData

from pipeline.cache import async_lru_cache
from pipeline.day_types import fallback_day_type, public_holidays_between
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC, build_stop_visits_ch_sql
from pipeline.live_filing import require_retained
from pipeline.planned_trips import plan_ctes
from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from pipeline.route_catalog import route_code_sql
from pipeline.service_day import clock_to_sec, filed_between_ch
from pipeline.strategies.static_join import CoverageGap, rt_field_gap
from pipeline.timetable_sampling import DayType

PairDayType = DayType

# The rider survey's tolerable waits (5, 10 and 20 minutes), as shares of the
# realised waits at or under each.
WAIT_BANDS_SEC: tuple[int, ...] = (300, 600, 1200)
BUFFER_SHARE = 0.95
WORST_DAYS = 5


class PairSpec(NamedTuple):
    """The parts of a transfer pair the figures depend on; a cache key, so an
    edit to any of them is a different entry."""

    from_agency_id: int
    from_route_code: str
    from_direction: int | None
    from_stop_ids: tuple[str, ...]
    to_agency_id: int
    to_route_code: str
    to_direction: int | None
    to_stop_ids: tuple[str, ...]
    walk_sec: int


class StopEvent(NamedTuple):
    """One planned call at a hub stop: a trip's stop on one service day, at
    ``sec`` seconds of that day."""

    day: date
    trip_id: str
    stop_sequence: int
    sec: int


Key = tuple[date, str, int]


def _key(e: StopEvent) -> Key:
    return (e.day, e.trip_id, e.stop_sequence)


Outcome = Literal["held", "missed", "unobserved"]


class Connection(NamedTuple):
    """A's arrival and the B departure planned for it. ``wait_sec`` is the
    realised wait, None when it is not known; ``need_sec`` is the planned
    buffer this connection needed to hold, None unless both sides were
    observed. ``stranded`` marks a missed connection with no later B
    departure that day."""

    day: date
    hour: int
    outcome: Outcome
    wait_sec: int | None
    need_sec: int | None
    stranded: bool


class _DayDepartures(NamedTuple):
    """One service day's B departures in scheduled order, with each one's
    scheduled second and predicted second (None where unobserved)."""

    events: list[StopEvent]
    secs: list[int]
    predicted: list[int | None]


def _departures_by_day(departures: Sequence[StopEvent], dep_delay: Mapping[Key, int]) -> dict[date, _DayDepartures]:
    grouped: dict[date, list[StopEvent]] = defaultdict(list)
    for d in departures:
        grouped[d.day].append(d)
    out = {}
    for day, events in grouped.items():
        events.sort(key=lambda e: (e.sec, e.trip_id, e.stop_sequence))
        predicted = [None if (delay := dep_delay.get(_key(e))) is None else e.sec + delay for e in events]
        out[day] = _DayDepartures(events, [e.sec for e in events], predicted)
    return out


def connections(
    arrivals: Sequence[StopEvent],
    departures: Sequence[StopEvent],
    arr_delay: Mapping[Key, int],
    dep_delay: Mapping[Key, int],
    walk_sec: int,
) -> tuple[list[Connection], int]:
    """Every planned connection, and how many of A's arrivals had none.

    Per service day, an arrival at ``a`` plans the first B departure at or
    after ``a + walk``; one with no such departure that day has no connection.
    It held when B's predicted departure is at or after A's predicted arrival
    plus the walk, and missed otherwise; without a prediction for either side
    it is unobserved, never held or missed.

    The realised wait runs from A's predicted arrival to the first B
    departure a rider could still catch, the walk included. B's trips are
    read in scheduled order from the planned one; an unobserved trip met
    before a catchable one leaves the wait unknown, since it may have been
    the one caught. An earlier trip predicted to leave late enough to catch
    counts too: a rider would take it, even when no later trip runs.

    Predicted delays lie within ``MAX_PLAUSIBLE_DELAY_SEC`` of the schedule
    (pipeline.db.plausible_delay_ch), so only the earlier trips scheduled
    within that reach of the catchable moment are looked at, and the forward
    scan ends at the first observed trip scheduled that far past it.
    """
    by_day_b = _departures_by_day(departures, dep_delay)
    empty = _DayDepartures([], [], [])
    out: list[Connection] = []
    no_onward = 0
    for a in sorted(arrivals, key=lambda e: (e.day, e.sec, e.trip_id, e.stop_sequence)):
        day_b = by_day_b.get(a.day, empty)
        planned = bisect_left(day_b.secs, a.sec + walk_sec)
        if planned == len(day_b.secs):
            no_onward += 1
            continue
        out.append(_connection(a, day_b, planned, arr_delay, walk_sec))
    return out, no_onward


def _connection(
    a: StopEvent, day_b: _DayDepartures, planned: int, arr_delay: Mapping[Key, int], walk_sec: int
) -> Connection:
    hour = a.sec // 3600
    a_delay = arr_delay.get(_key(a))
    b_predicted = day_b.predicted[planned]
    if a_delay is None or b_predicted is None:
        return Connection(a.day, hour, "unobserved", None, None, False)
    alighted = a.sec + a_delay
    catchable_from = alighted + walk_sec
    b_delay = b_predicted - day_b.secs[planned]
    reach = bisect_left(day_b.secs, catchable_from - MAX_PLAUSIBLE_DELAY_SEC, hi=planned)
    earlier = [p for p in day_b.predicted[reach:planned] if p is not None and p >= catchable_from]
    caught, stranded = _first_catchable(day_b.predicted, planned, catchable_from)
    if earlier and (caught is not None or stranded):
        caught = min(earlier) if caught is None else min(caught, *earlier)
        stranded = False
    return Connection(
        a.day,
        hour,
        "held" if b_predicted >= catchable_from else "missed",
        None if caught is None else caught - alighted,
        a_delay - b_delay + walk_sec,
        stranded,
    )


def _first_catchable(predicted: Sequence[int | None], planned: int, catchable_from: int) -> tuple[int | None, bool]:
    """The first predicted departure from ``planned`` on, in scheduled order,
    at or after ``catchable_from``: (its second, False); (None, False) when an
    unobserved trip comes first; (None, True) when none is left that day."""
    for i in range(planned, len(predicted)):
        p = predicted[i]
        if p is None:
            return None, False
        if p >= catchable_from:
            return p, False
    return None, True


def percentile_cont(sorted_values: Sequence[int], q: float) -> float:
    """Linear interpolation between the closest ranks, as Postgres's
    ``percentile_cont`` (the segment-runtime report's percentiles)."""
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def buffer_needed(needs: Sequence[int], walk_sec: int) -> int:
    """The smallest planned buffer, in whole minutes, under which at least
    ``BUFFER_SHARE`` of the observed connections would have held.

    A connection holds when B's planned departure minus A's planned arrival
    is at least its need: A's lateness minus B's lateness, plus the walk. The
    nearest-rank percentile of the needs is the smallest value at least that
    share of them do not exceed, which an interpolated one does not promise.
    No planned connection is shorter than the walk, so neither is the buffer.
    It treats each day's delays as given, as if B kept its delay under
    another buffer."""
    ordered = sorted(needs)
    rank = math.ceil(BUFFER_SHARE * len(ordered))
    return math.ceil(max(ordered[rank - 1], walk_sec) / 60)


@dataclass
class Tally:
    planned: int = 0
    held: int = 0
    missed: int = 0
    unobserved: int = 0
    stranded: int = 0

    def add(self, c: Connection) -> None:
        self.planned += 1
        if c.outcome == "held":
            self.held += 1
        elif c.outcome == "missed":
            self.missed += 1
            self.stranded += c.stranded
        else:
            self.unobserved += 1

    @property
    def observed(self) -> int:
        return self.held + self.missed

    @property
    def held_share(self) -> float | None:
        return None if self.observed == 0 else self.held / self.observed

    @property
    def small_sample(self) -> bool:
        return self.observed < MIN_GROUP_SAMPLES


@dataclass
class Reliability:
    """The pair's figures over the requested days. Every share, percentile
    and the buffer is None below ``MIN_GROUP_SAMPLES`` of what it is taken
    over: held share and buffer over the observed connections, the wait
    figures over the known waits."""

    total: Tally
    held_share: float | None
    no_onward: int
    waits: int
    wait_p50_sec: float | None
    wait_p90_sec: float | None
    within_band_share: dict[int, float] | None
    buffer_min: int | None
    by_hour: dict[int, Tally]
    by_day: dict[date, Tally]
    worst_days: list[date]
    days: list[date] = field(default_factory=list)
    no_plan_days: list[date] = field(default_factory=list)
    arrivals_gap: CoverageGap | None = None


def summarise(
    found: Iterable[Connection], no_onward: int, walk_sec: int, days: list[date], no_plan_days: list[date]
) -> Reliability:
    total = Tally()
    by_hour: dict[int, Tally] = defaultdict(Tally)
    by_day: dict[date, Tally] = {d: Tally() for d in days}
    waits: list[int] = []
    needs: list[int] = []
    for c in found:
        total.add(c)
        by_hour[c.hour].add(c)
        by_day.setdefault(c.day, Tally()).add(c)
        if c.wait_sec is not None:
            waits.append(c.wait_sec)
        if c.need_sec is not None:
            needs.append(c.need_sec)
    waits.sort()
    waits_known = len(waits) >= MIN_GROUP_SAMPLES
    return Reliability(
        total=total,
        held_share=None if total.small_sample else total.held_share,
        no_onward=no_onward,
        waits=len(waits),
        wait_p50_sec=percentile_cont(waits, 0.5) if waits_known else None,
        wait_p90_sec=percentile_cont(waits, 0.9) if waits_known else None,
        within_band_share=(
            {band: sum(w <= band for w in waits) / len(waits) for band in WAIT_BANDS_SEC} if waits_known else None
        ),
        buffer_min=None if total.small_sample else buffer_needed(needs, walk_sec),
        by_hour=dict(sorted(by_hour.items())),
        by_day=dict(sorted(by_day.items())),
        worst_days=worst_days(by_day),
        days=days,
        no_plan_days=no_plan_days,
    )


def worst_days(by_day: Mapping[date, Tally]) -> list[date]:
    """The days with the most missed connections, then the lowest held share,
    then the earliest. Ranked on the missed count, which is a count of events
    rather than an estimate, so a day needs no sample floor to be listed."""
    missed = [(d, t) for d, t in by_day.items() if t.missed > 0]
    missed.sort(key=lambda item: (-item[1].missed, item[1].held_share or 0.0, item[0]))
    return [d for d, _ in missed[:WORST_DAYS]]


def days_of_type(from_date: date, to_date: date, day_type: PairDayType) -> list[date]:
    """The dates of ``day_type`` in the range, by the weekday rule
    (pipeline.day_types.fallback_day_type): the same for both operators,
    whose own calendars may split the days differently."""
    holidays = public_holidays_between(from_date, to_date)
    span = (to_date - from_date).days
    days = [date.fromordinal(from_date.toordinal() + i) for i in range(span + 1)]
    return [d for d in days if fallback_day_type(d, holidays) == day_type]


# A trip's first stop is where it starts, so nobody arrives there on it, and
# its last is where it ends, so it departs nowhere (the rule of
# pipeline.timetable_sampling.stop_departures): $8 says which side is read.
_PLAN_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$3::date")
    + f"""
    SELECT r.day, st.trip_id, st.stop_sequence, st.arrival_time, st.departure_time
    FROM running r
    JOIN static_trips t ON t.agency_id = r.agency_id AND t.service_id = r.service_id
    JOIN static_stop_times st ON st.agency_id = t.agency_id AND st.trip_id = t.trip_id
    JOIN LATERAL (
        SELECT min(x.stop_sequence) AS first_sequence, max(x.stop_sequence) AS last_sequence
        FROM static_stop_times x
        WHERE x.agency_id = t.agency_id AND x.trip_id = t.trip_id
    ) ends ON TRUE
    WHERE {route_code_sql("t.route_id")} = $4
      AND ($5::smallint IS NULL OR t.direction_id = $5)
      AND st.stop_id = ANY($6::text[])
      AND r.day = ANY($7::date[])
      AND CASE WHEN $8::bool THEN st.stop_sequence > ends.first_sequence
               ELSE st.stop_sequence < ends.last_sequence END
    """
)

_COVERED_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$3::date")
    + " SELECT DISTINCT day FROM covered WHERE day = ANY($4::date[])"
)


async def _covered(conn, agency_id: int, days: list[date]) -> set[date]:
    rows = await conn.fetch(_COVERED_SQL, agency_id, days[0], days[-1], days)
    return {r["day"] for r in rows}


async def _plan(
    conn,
    agency_id: int,
    route_code: str,
    direction: int | None,
    stops: Sequence[str],
    days: list[date],
    *,
    arriving: bool,
) -> list[StopEvent]:
    rows = await conn.fetch(_PLAN_SQL, agency_id, days[0], days[-1], route_code, direction, list(stops), days, arriving)
    events = []
    for r in rows:
        first, second = (r["arrival_time"], r["departure_time"])
        sec = clock_to_sec(first if arriving else second) or clock_to_sec(second if arriving else first)
        if sec is not None:
            events.append(StopEvent(r["day"], r["trip_id"], r["stop_sequence"], sec))
    return events


def visits_sql() -> str:
    """Each visit of the planned (trip, stop sequence) pairs over a span of
    service days, with its predicted departure and arrival delays.

    Bounded by trip and stop sequence rather than by stop id: `stop_id` is an
    optional realtime field, and a feed that leaves it out would read every
    connection unobserved. The pairs come as the external table ``planned``
    (:func:`_planned_table`), which grows with the window without meeting the
    size limit on a bound parameter."""
    where = "(u.trip_id, u.stop_sequence) IN (SELECT trip_id, stop_sequence FROM planned) AND " + filed_between_ch(
        "{from_date:Date}", "{to_date:Date}"
    )
    visits = build_stop_visits_ch_sql(extra_where=where)
    return f"SELECT trip_id, date, stop_sequence, dep_delay, arr_delay FROM ({visits})"


def _planned_table(events: Iterable[StopEvent]) -> ExternalData:
    pairs = sorted({(e.trip_id, e.stop_sequence) for e in events})
    rows = "\n".join(json.dumps({"trip_id": t, "stop_sequence": q}, ensure_ascii=False) for t, q in pairs)
    return ExternalData(
        file_name="planned",
        data=rows.encode(),
        fmt="JSONEachRow",
        structure=["trip_id String", "stop_sequence UInt16"],
    )


Visit = tuple[str, date, int, int | None, int | None]


async def _observed(ch, agency_id: int, events: Sequence[StopEvent], days: list[date]) -> list[Visit]:
    if not events:
        return []
    result = await ch.query(
        visits_sql(),
        parameters={"agency_id": agency_id, "from_date": days[0], "to_date": days[-1]},
        external_data=_planned_table(events),
    )
    return [(str(t), d, int(s), dep, arr) for t, d, s, dep, arr in result.result_rows]


def delays(rows: Iterable[Visit], wanted: set[date], *, arriving: bool, arrivals_confirmed: bool) -> dict[Key, int]:
    """Each observed visit's delay on the wanted days. For A, the arrival's
    where the agency's feed is confirmed to send arrivals, else the
    departure's; for B, the departure's."""
    out: dict[Key, int] = {}
    for trip_id, day, seq, dep, arr in rows:
        if day not in wanted:
            continue
        value = arr if arriving and arrivals_confirmed and arr is not None else dep
        if value is not None:
            out[(day, trip_id, seq)] = int(value)
    return out


@async_lru_cache(maxsize=64, ttl_seconds=600)
async def compute_reliability(
    conn, ch, pair: PairSpec, from_date: date, to_date: date, day_type: PairDayType
) -> Reliability:
    """The pair's reliability over the days of ``day_type`` in the range."""
    days = days_of_type(from_date, to_date, day_type)
    if not days:
        return summarise([], 0, pair.walk_sec, [], [])
    agencies = sorted({pair.from_agency_id, pair.to_agency_id})
    for agency_id in agencies:
        await require_retained(conn, agency_id, days[0])
    covered = await _covered(conn, agencies[0], days)
    if len(agencies) > 1:
        covered &= await _covered(conn, agencies[1], days)
    planned_days = [d for d in days if d in covered]
    no_plan = [d for d in days if d not in covered]
    if not planned_days:
        return summarise([], 0, pair.walk_sec, [], no_plan)
    arrivals = await _plan(
        conn,
        pair.from_agency_id,
        pair.from_route_code,
        pair.from_direction,
        pair.from_stop_ids,
        planned_days,
        arriving=True,
    )
    departures = await _plan(
        conn,
        pair.to_agency_id,
        pair.to_route_code,
        pair.to_direction,
        pair.to_stop_ids,
        planned_days,
        arriving=False,
    )
    # Arrival delays are an optional realtime field, read only from a feed
    # confirmed to send them (pipeline.strategies.static_join); the other
    # optional fields are not read here, so their verdicts do not matter.
    arrivals_gap = await rt_field_gap(pair.from_agency_id, conn, "arr_delay")
    arrivals_confirmed = arrivals_gap is None
    if len(agencies) == 1:
        rows = await _observed(ch, pair.from_agency_id, [*arrivals, *departures], planned_days)
        a_rows, b_rows = _split(rows, arrivals), _split(rows, departures)
    else:
        a_rows = await _observed(ch, pair.from_agency_id, arrivals, planned_days)
        b_rows = await _observed(ch, pair.to_agency_id, departures, planned_days)
    wanted = set(planned_days)
    arr = delays(a_rows, wanted, arriving=True, arrivals_confirmed=arrivals_confirmed)
    dep = delays(b_rows, wanted, arriving=False, arrivals_confirmed=arrivals_confirmed)
    result = await asyncio.to_thread(_reliability, arrivals, departures, arr, dep, pair.walk_sec, planned_days, no_plan)
    result.arrivals_gap = arrivals_gap
    return result


def _split(rows: Iterable[Visit], events: Iterable[StopEvent]) -> list[Visit]:
    """The rows of one side of a same-operator pair: those at a (trip, stop
    sequence) that side plans."""
    planned = {(e.trip_id, e.stop_sequence) for e in events}
    return [r for r in rows if (r[0], r[2]) in planned]


def _reliability(
    arrivals: list[StopEvent],
    departures: list[StopEvent],
    arr: dict[Key, int],
    dep: dict[Key, int],
    walk_sec: int,
    days: list[date],
    no_plan: list[date],
) -> Reliability:
    found, no_onward = connections(arrivals, departures, arr, dep, walk_sec)
    return summarise(found, no_onward, walk_sec, days, no_plan)
