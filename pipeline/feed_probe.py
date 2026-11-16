"""Probe a GTFS-RT feed against its static timetable and decide whether the
generic strategies can ingest it.

``gtfs_pipeline.py add_agency --feed-url … --static-url …`` runs it. Nothing
writes until :func:`add_probed_agency`, so a caller can show the report and
stop.

The static zip is fetched by ``direct_url.fetch``, ``latest.zip`` preference
included, so the timetable measured is the one ``refresh-static`` then loads.
The feed is fetched by the coverage re-probe's own fetch, and its coverage is
judged by ``static_join.assess_field_coverage``, so this verdict and the
scorecard's cannot disagree. An accepted feed gets ``static_join``/
``direct_url`` written explicitly: an agency whose ``ingest_strategy`` is
NULL is read as ``aomori_regex`` (``pipeline.strategies.get_ingest_strategy``).
"""

from __future__ import annotations

import csv
import io
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pipeline import rt_coverage_reprobe
from pipeline.strategies import direct_url
from pipeline.strategies._pb import _fields, is_feed_message
from pipeline.strategies.aomori_regex import _TRIP_RE_DEFAULT
from pipeline.strategies.errors import StaticFetchError
from pipeline.strategies.static_join import (
    RT_COVERAGE_FIELDS,
    assess_field_coverage,
    decode_trip_updates,
    field_coverage,
    record_field_coverage_probe,
)
from pipeline.url_guard import FETCH_ERRORS, FeedURLError, redact_url, validate_feed_url

INGEST_STRATEGY = "static_join"
STATIC_STRATEGY = "direct_url"

#: The least share of a probe's distinct TripUpdate trip_ids that must be in
#: ``trips.txt``. ``static_join`` takes each row's schedule from that join,
#: and its ingest already warns once half of a poll misses
#: (``static_join._MISS_WARN_FRACTION``), the sign of a timetable keyed
#: differently from the feed. Onboarding asks for a clearly stricter bar,
#: leaving room for the added and unscheduled trips a real feed carries that
#: no timetable lists.
JOIN_RATE_MIN = 0.8

DEFAULT_PROBE_SECONDS = 120.0
DEFAULT_PROBE_INTERVAL_SEC = 30.0

#: How many unmatched trip_ids a refusal quotes.
UNMATCHED_SAMPLE = 5

# FeedEntity fields: trip_update, vehicle, alert.
_ENTITY_TRIP_UPDATE, _ENTITY_VEHICLE, _ENTITY_ALERT = 3, 4, 5


class Verdict(StrEnum):
    """What a probe concluded. Two accept; every other one adds nothing."""

    ACCEPT = "accept"
    ACCEPT_EMPTY = "accept_empty"
    URL_REJECTED = "url_rejected"
    STATIC_UNAVAILABLE = "static_unavailable"
    STATIC_INCOMPLETE = "static_incomplete"
    NAME_REQUIRED = "name_required"
    FEED_UNREACHABLE = "feed_unreachable"
    NOT_A_FEED = "not_a_feed"
    EMPTY = "empty"
    NO_TRIP_UPDATES = "no_trip_updates"
    NO_TRIP_IDS = "no_trip_ids"
    LOW_JOIN = "low_join"


_ACCEPTED = frozenset({Verdict.ACCEPT, Verdict.ACCEPT_EMPTY})


@dataclass(frozen=True)
class StaticAgency:
    """One ``agency.txt`` row."""

    name: str
    timezone: str | None


@dataclass(frozen=True)
class StaticSummary:
    """What a static zip offers the join. ``None`` marks a file the zip lacks."""

    agencies: tuple[StaticAgency, ...] | None
    trip_ids: frozenset[str] | None
    stop_ids: frozenset[str] | None


@dataclass(frozen=True)
class Poll:
    """One poll of the feed. ``error`` is set when it was not measured."""

    error: str | None = None
    not_a_feed: bool = False
    entities: int = 0
    trip_updates: int = 0
    vehicles: int = 0
    alerts: int = 0
    trip_updates_without_trip_id: int = 0
    trip_ids: frozenset[str] = frozenset()
    stop_ids: frozenset[str] = frozenset()
    coverage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RtProbe:
    """Every poll of one probe, and what they add up to."""

    polls: tuple[Poll, ...]

    @property
    def measured(self) -> tuple[Poll, ...]:
        return tuple(p for p in self.polls if p.error is None)

    @property
    def trip_ids(self) -> frozenset[str]:
        return frozenset().union(*(p.trip_ids for p in self.measured))

    @property
    def stop_ids(self) -> frozenset[str]:
        return frozenset().union(*(p.stop_ids for p in self.measured))

    def total(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.measured)

    def busiest(self) -> Poll | None:
        """The measured poll with the most stop_time_updates: the one whose
        coverage is recorded, since one poll is what the re-probe records."""
        return max(self.measured, key=lambda p: p.coverage.get("stop_time_updates", 0), default=None)


@dataclass(frozen=True)
class Decision:
    """A verdict, the reason stated for a person, and what an accepted one writes."""

    verdict: Verdict
    reason: str
    name: str | None = None
    record_coverage: bool = False

    @property
    def accepted(self) -> bool:
        return self.verdict in _ACCEPTED

    @property
    def ingest_strategy(self) -> str | None:
        return INGEST_STRATEGY if self.accepted else None

    @property
    def static_strategy(self) -> str | None:
        return STATIC_STRATEGY if self.accepted else None


@dataclass(frozen=True)
class ProbeOutcome:
    """A probe's evidence and its decision. ``static``/``rt`` are None for a
    step the probe never reached."""

    feed_url: str
    static_url: str
    decision: Decision
    static: StaticSummary | None = None
    rt: RtProbe | None = None


def _rows(zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    with zf.open(filename) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def read_static_summary(zip_path: Path) -> StaticSummary:
    """The agencies, trip_ids and stop_ids of a GTFS zip. Raises
    ``zipfile.BadZipFile`` for a file that is not a zip."""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        agencies = (
            tuple(
                StaticAgency((r.get("agency_name") or "").strip(), (r.get("agency_timezone") or "").strip() or None)
                for r in _rows(zf, "agency.txt")
            )
            if "agency.txt" in names
            else None
        )

        def ids(filename: str, column: str) -> frozenset[str] | None:
            if filename not in names:
                return None
            return frozenset(v for r in _rows(zf, filename) if (v := (r.get(column) or "").strip()))

        return StaticSummary(agencies, ids("trips.txt", "trip_id"), ids("stops.txt", "stop_id"))


def measure_poll(raw: bytes) -> Poll:
    """Decode one poll. Bytes that are not a GTFS-RT FeedMessage come back as
    a ``not_a_feed`` error rather than as an empty feed, which they would
    otherwise decode to."""
    if not is_feed_message(raw):
        return Poll(error=f"{len(raw)} bytes that are not a GTFS-RT FeedMessage", not_a_feed=True)
    entities = [_fields(e) for e in _fields(raw).get(2, [])]
    decoded = list(decode_trip_updates(raw))
    n_trip_updates = sum(1 for e in entities if _ENTITY_TRIP_UPDATE in e)
    return Poll(
        entities=len(entities),
        trip_updates=n_trip_updates,
        vehicles=sum(1 for e in entities if _ENTITY_VEHICLE in e),
        alerts=sum(1 for e in entities if _ENTITY_ALERT in e),
        trip_updates_without_trip_id=n_trip_updates - len(decoded),
        trip_ids=frozenset(tu.trip_id for tu in decoded),
        stop_ids=frozenset(u.stop_id for tu in decoded for u in tu.stop_time_updates if u.stop_id),
        coverage=field_coverage(raw),
    )


def poll_feed(
    feed_url: str,
    *,
    seconds: float,
    interval: float,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RtProbe:
    """Poll *feed_url* once now and once every *interval* seconds within
    *seconds*. A failed fetch is kept as an errored poll, not raised."""
    fetch = fetch or rt_coverage_reprobe.fetch_feed
    n_polls = 1 + int(seconds // interval) if interval > 0 else 1
    polls: list[Poll] = []
    for i in range(n_polls):
        if i:
            sleep(interval)
        try:
            raw = fetch(feed_url)
        except FETCH_ERRORS as exc:
            polls.append(Poll(error=f"fetch failed: {exc}"))
            continue
        try:
            polls.append(measure_poll(raw))
        except Exception as exc:
            # A body that passes as a FeedMessage can still carry a string
            # field that is not UTF-8, which only the full decode meets.
            polls.append(Poll(error=f"does not decode as GTFS-RT ({exc})", not_a_feed=True))
    return RtProbe(tuple(polls))


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def static_refusal(static: StaticSummary, name: str | None) -> Decision | None:
    """The refusal a static zip alone earns, or None when it can be joined to
    and the agency named."""
    if static.trip_ids is None:
        return Decision(Verdict.STATIC_INCOMPLETE, "the static zip has no trips.txt, so no trip_id can be joined")
    if name:
        return None
    if static.agencies is None:
        return Decision(Verdict.NAME_REQUIRED, "the static zip has no agency.txt to name the agency; pass --name")
    named = [a.name for a in static.agencies if a.name]
    if len(static.agencies) > 1:
        return Decision(
            Verdict.NAME_REQUIRED,
            f"agency.txt lists {len(static.agencies)} agencies ({', '.join(named)}); "
            "pass --name for the agency this feed is added as",
        )
    if not named:
        return Decision(Verdict.NAME_REQUIRED, "agency.txt gives no agency_name; pass --name")
    return None


def resolved_name(static: StaticSummary, name: str | None) -> str | None:
    """*name*, else the only agency.txt agency's name."""
    if name:
        return name
    if static.agencies and len(static.agencies) == 1:
        return static.agencies[0].name or None
    return None


def decide(static: StaticSummary, rt: RtProbe, *, name: str | None, accept_empty: bool = False) -> Decision:
    """Whether ``static_join``/``direct_url`` can ingest this feed, and why not."""
    refused = static_refusal(static, name)
    if refused is not None:
        return refused
    agency_name = resolved_name(static, name)

    def named(verdict: Verdict, reason: str, *, record_coverage: bool = False) -> Decision:
        return Decision(verdict, reason, name=agency_name, record_coverage=record_coverage)

    measured = rt.measured
    if not measured:
        if rt.polls and all(p.not_a_feed for p in rt.polls):
            return named(
                Verdict.NOT_A_FEED,
                f"no poll of {len(rt.polls)} was a GTFS-RT FeedMessage ({rt.polls[0].error}); check the feed URL",
            )
        return named(
            Verdict.FEED_UNREACHABLE,
            f"no poll of {len(rt.polls)} could be read; last: {rt.polls[-1].error if rt.polls else 'none made'}",
        )
    if rt.total("entities") == 0:
        if accept_empty:
            return named(
                Verdict.ACCEPT_EMPTY,
                f"the feed sent no entities in {len(measured)} polls; added with {INGEST_STRATEGY} as asked, "
                "and the hourly re-probe records its coverage verdict once the feed is in service",
            )
        return named(
            Verdict.EMPTY,
            f"the feed sent no entities in {len(measured)} polls; probe again in service hours, "
            "or pass --accept-empty to add it now",
        )
    if rt.total("trip_updates") == 0:
        return named(
            Verdict.NO_TRIP_UPDATES,
            f"the feed sent {rt.total('vehicles')} VehiclePositions and {rt.total('alerts')} Alerts but no "
            f"TripUpdates in {len(measured)} polls; {INGEST_STRATEGY} ingests TripUpdates, "
            "so point --feed-url at the TripUpdates feed",
        )
    trip_ids = rt.trip_ids
    if not trip_ids:
        return named(
            Verdict.NO_TRIP_IDS,
            f"none of the feed's {rt.total('trip_updates')} TripUpdates names a trip_id, "
            f"and {INGEST_STRATEGY} joins the timetable on trip_id",
        )
    assert static.trip_ids is not None
    matched = trip_ids & static.trip_ids
    if len(matched) < JOIN_RATE_MIN * len(trip_ids):
        unmatched = sorted(trip_ids - static.trip_ids)
        patterned = sum(1 for t in unmatched if _TRIP_RE_DEFAULT.match(t))
        hint = (
            f"{patterned} of them match aomori_regex's trip_id pattern, so the feed may need that strategy"
            if patterned
            else "the feed may need a trip_id pattern (aomori_regex) or a different static zip"
        )
        return named(
            Verdict.LOW_JOIN,
            f"{_pct(len(matched), len(trip_ids))} of {len(trip_ids)} trip_ids are in trips.txt, "
            f"below the {JOIN_RATE_MIN:.0%} needed; sample unmatched: {', '.join(unmatched[:UNMATCHED_SAMPLE])}; "
            f"{hint}",
        )
    busiest = rt.busiest()
    sample = busiest.coverage.get("stop_time_updates", 0) if busiest else 0
    record = sample >= rt_coverage_reprobe.MIN_SAMPLE
    coverage_note = (
        "its coverage verdict is recorded from the busiest poll"
        if record
        else f"the busiest poll held {sample} stop_time_updates, under the {rt_coverage_reprobe.MIN_SAMPLE} "
        "a verdict needs, so the hourly re-probe records it"
    )
    return named(
        Verdict.ACCEPT,
        f"{_pct(len(matched), len(trip_ids))} of {len(trip_ids)} trip_ids are in trips.txt; {coverage_note}",
        record_coverage=record,
    )


def probe(
    feed_url: str,
    static_url: str,
    *,
    name: str | None = None,
    seconds: float = DEFAULT_PROBE_SECONDS,
    interval: float = DEFAULT_PROBE_INTERVAL_SEC,
    accept_empty: bool = False,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ProbeOutcome:
    """Validate, fetch the static zip, poll the feed and decide. Writes
    nothing: the zip is fetched into a temporary directory that is gone on
    return. A static zip that settles the refusal on its own ends the probe
    before the feed is polled."""

    def outcome(decision: Decision, static: StaticSummary | None = None, rt: RtProbe | None = None) -> ProbeOutcome:
        return ProbeOutcome(feed_url, static_url, decision, static, rt)

    for label, url in (("--feed-url", feed_url), ("--static-url", static_url)):
        try:
            validate_feed_url(url)
        except FeedURLError as exc:
            return outcome(Decision(Verdict.URL_REJECTED, f"{label}: {exc}"))

    with tempfile.TemporaryDirectory(prefix="feed_probe_") as staging:
        try:
            zip_path = direct_url.fetch(0, static_url, Path(staging), force=True)
        except StaticFetchError as exc:
            return outcome(Decision(Verdict.STATIC_UNAVAILABLE, f"the static zip could not be fetched: {exc}"))
        if zip_path is None:
            return outcome(Decision(Verdict.STATIC_UNAVAILABLE, "the static URL sent no zip"))
        try:
            static = read_static_summary(zip_path)
        except zipfile.BadZipFile:
            not_a_zip = "the static URL answered with a file that is not a zip"
            return outcome(Decision(Verdict.STATIC_UNAVAILABLE, not_a_zip))

    refused = static_refusal(static, name)
    if refused is not None:
        return outcome(refused, static)
    rt = poll_feed(feed_url, seconds=seconds, interval=interval, fetch=fetch, sleep=sleep)
    return outcome(decide(static, rt, name=name, accept_empty=accept_empty), static, rt)


def report_lines(o: ProbeOutcome) -> list[str]:
    """The probe report a person reads. URLs are redacted: either can carry
    an API key."""
    lines = [f"feed:   {redact_url(o.feed_url)}", f"static: {redact_url(o.static_url)}"]
    if o.static is not None:
        s = o.static
        agencies = (
            "no agency.txt"
            if s.agencies is None
            else "; ".join(f"{a.name or '(unnamed)'} ({a.timezone or 'no timezone'})" for a in s.agencies)
        )
        lines.append(f"  agency.txt: {agencies}")
        lines.append(f"  trips.txt: {'missing' if s.trip_ids is None else f'{len(s.trip_ids)} trip_ids'}")
        lines.append(f"  stops.txt: {'missing' if s.stop_ids is None else f'{len(s.stop_ids)} stop_ids'}")
    if o.rt is not None:
        lines.extend(_rt_lines(o.rt, o.static))
    lines.append(f"verdict: {o.decision.verdict.value}: {o.decision.reason}")
    if o.decision.accepted:
        lines.append(
            f"  would add {o.decision.name!r} with ingest_strategy={o.decision.ingest_strategy}, "
            f"static_strategy={o.decision.static_strategy}"
        )
    return lines


def _rt_lines(rt: RtProbe, static: StaticSummary | None) -> Iterable[str]:
    yield f"  polls: {len(rt.measured)} of {len(rt.polls)} measured"
    for i, p in enumerate(rt.polls, 1):
        if p.error is not None:
            yield f"    poll {i}: {p.error}"
            continue
        no_id = f", {p.trip_updates_without_trip_id} without a trip_id" if p.trip_updates_without_trip_id else ""
        yield (
            f"    poll {i}: {p.entities} entities ({p.trip_updates} TripUpdates{no_id}, {p.vehicles} VehiclePositions, "
            f"{p.alerts} Alerts), {p.coverage.get('stop_time_updates', 0)} stop_time_updates"
        )
    if static is not None and static.trip_ids is not None and rt.trip_ids:
        hit, n = len(rt.trip_ids & static.trip_ids), len(rt.trip_ids)
        yield f"  trip_id join: {hit} of {n} distinct trip_ids in trips.txt ({_pct(hit, n)})"
    if static is not None and static.stop_ids is not None and rt.stop_ids:
        hit, n = len(rt.stop_ids & static.stop_ids), len(rt.stop_ids)
        yield f"  stop_id join: {hit} of {n} distinct stop_ids in stops.txt ({_pct(hit, n)})"
    busiest = rt.busiest()
    if busiest is None:
        return
    verdicts = assess_field_coverage(busiest.coverage)
    if verdicts is None:
        yield "  RT field coverage: no stop_time_updates to assess"
        return
    yield f"  RT field coverage (busiest poll, {busiest.coverage['stop_time_updates']} stop_time_updates):"
    for f in RT_COVERAGE_FIELDS:
        yield f"    {f}: {busiest.coverage[f'{f}_coverage']:.0%} ({'confirmed' if verdicts[f] else 'refuted'})"


_INSERT_AGENCY_SQL = """
    INSERT INTO agencies (agency_name, feed_url, static_url, ingest_strategy, static_strategy)
    VALUES ($1, $2, $3, $4, $5)
    RETURNING agency_id
"""


async def add_probed_agency(conn, o: ProbeOutcome) -> int:
    """Insert the agency an accepted probe describes, with its strategies and,
    when the decision says so, its coverage verdict, in one transaction on
    the asyncpg *conn*. Returns the new agency_id.

    Raises ``ValueError`` for a refused probe, which writes nothing. The
    timetable is loaded afterwards: ``load_static`` commits on its own and
    writes ClickHouse, so it cannot share this transaction.
    """
    d = o.decision
    if not d.accepted:
        raise ValueError(f"the probe refused this feed: {d.reason}")
    busiest = o.rt.busiest() if o.rt is not None else None
    async with conn.transaction():
        agency_id = await conn.fetchval(
            _INSERT_AGENCY_SQL, d.name, o.feed_url, o.static_url, d.ingest_strategy, d.static_strategy
        )
        if d.record_coverage and busiest is not None:
            await record_field_coverage_probe(conn, agency_id, busiest.coverage, o.feed_url)
    return agency_id
