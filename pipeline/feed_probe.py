"""Probe a GTFS-RT feed against its static timetable, decide whether the
generic strategies can ingest it, and onboard it when they can.

``gtfs_pipeline.py add_agency --feed-url … --static-url …`` runs :func:`onboard`.
Nothing writes until :func:`add_probed_agency`, so a caller can show the
report and stop.

The probe measures the join ``static_join.parse_feed`` itself makes: each
StopTimeUpdate, decoded by ``decode_trip_updates``, joins the timetable on
``(trip_id, stop_sequence)`` in ``stop_times.txt`` and on its trip in
``trips.txt``, read through the loader's own row reader. The static zip is
fetched once, by ``direct_url.fetch``, and the zip measured is the zip
loaded. The feed is fetched by the coverage re-probe's own fetch, and its
coverage judged by ``static_join.assess_field_coverage``, so this verdict
and the scorecard's cannot disagree. An accepted feed gets
``static_join``/``direct_url`` written explicitly: an agency whose
``ingest_strategy`` is NULL is read as ``aomori_regex``
(``pipeline.strategies.get_ingest_strategy``).

:class:`Decision` carries facts only; :func:`report_lines` renders them in
English for the command line.
"""

from __future__ import annotations

import asyncio
import csv
import io
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline import rt_coverage_reprobe
from pipeline.poller import DEFAULT_INTERVAL_SEC
from pipeline.static_loader import iter_static_rows
from pipeline.strategies import aomori_regex, direct_url
from pipeline.strategies._pb import _dec, _fields, is_feed_message
from pipeline.strategies.errors import StaticFetchError
from pipeline.strategies.static_join import (
    RT_COVERAGE_FIELDS,
    assess_field_coverage,
    decode_trip_updates,
    field_coverage,
    record_field_coverage_probe,
)
from pipeline.url_guard import DEFAULT_MAX_BYTES, FETCH_ERRORS, FeedURLError, redact_url, validate_feed_url

INGEST_STRATEGY = "static_join"
STATIC_STRATEGY = "direct_url"

#: The least share of the probe's StopTimeUpdates that must join the
#: timetable. A row that misses is stored with no schedule, and every delay
#: figure reads the schedule; the remainder covers trips a timetable rightly
#: lacks, such as added services.
JOIN_RATE_MIN = 0.8

DEFAULT_PROBE_SECONDS = 120.0

#: How many misses a refusal quotes.
UNMATCHED_SAMPLE = 5

#: The most any one zip member may decompress to. The download is capped by
#: ``safe_urlopen``; this bounds what a small zip can expand into.
MAX_MEMBER_BYTES = DEFAULT_MAX_BYTES

#: The files ``static_join`` ingest needs rows in: ``require_timetable``
#: reads ``static_stops``, and the join reads the other two.
REQUIRED_FILES = ("trips.txt", "stops.txt", "stop_times.txt")

# FeedEntity fields: trip_update, vehicle, alert.
_ENTITY_TRIP_UPDATE, _ENTITY_VEHICLE, _ENTITY_ALERT = 3, 4, 5


class Verdict(StrEnum):
    """What a probe concluded. Two accept; every other one adds nothing."""

    ACCEPT = "accept"
    ACCEPT_EMPTY = "accept_empty"
    FEED_TAKEN = "feed_taken"
    URL_REJECTED = "url_rejected"
    STATIC_UNAVAILABLE = "static_unavailable"
    STATIC_UNREADABLE = "static_unreadable"
    STATIC_INCOMPLETE = "static_incomplete"
    NAME_REQUIRED = "name_required"
    FEED_UNREACHABLE = "feed_unreachable"
    NOT_A_FEED = "not_a_feed"
    EMPTY = "empty"
    NO_TRIP_UPDATES = "no_trip_updates"
    NO_TRIP_IDS = "no_trip_ids"
    NO_STOP_TIME_UPDATES = "no_stop_time_updates"
    STOP_ID_ONLY = "stop_id_only"
    LOW_JOIN = "low_join"


_ACCEPTED = frozenset({Verdict.ACCEPT, Verdict.ACCEPT_EMPTY})


@dataclass(frozen=True)
class StaticAgency:
    """One ``agency.txt`` row."""

    name: str
    timezone: str | None


@dataclass(frozen=True)
class StaticSummary:
    """What a static zip offers the join.

    ``rows`` maps each of ``REQUIRED_FILES`` to its data-row count, None when
    the zip lacks it. ``stop_time_keys`` holds only the ``(trip_id,
    stop_sequence)`` keys a caller asked :func:`read_static_summary` for.
    """

    agencies: tuple[StaticAgency, ...] | None
    rows: dict[str, int | None]
    trip_ids: frozenset[str]
    stop_ids: frozenset[str]
    stop_time_keys: frozenset[tuple[str, int]] = frozenset()


class StaticZipError(Exception):
    """A zip the probe cannot read, naming the member at fault."""

    def __init__(self, verdict: Verdict, file: str | None, detail: str):
        super().__init__(detail)
        self.verdict, self.file, self.detail = verdict, file, detail


@dataclass(frozen=True)
class Poll:
    """One poll of the feed. ``error`` is set when it was not measured.

    ``keys`` holds one ``(trip_id, stop_sequence)`` per StopTimeUpdate ingest
    would try to join. The StopTimeUpdates it cannot try are counted apart:
    those of TripUpdates naming no trip_id (at least one per such
    TripUpdate), and those with no usable stop_sequence, of which
    ``stop_id_only`` still carry a stop_id.
    """

    error: str | None = None
    not_a_feed: bool = False
    entities: int = 0
    trip_updates: int = 0
    vehicles: int = 0
    alerts: int = 0
    trip_updates_without_trip_id: int = 0
    trip_ids: frozenset[str] = frozenset()
    stop_ids: frozenset[str] = frozenset()
    keys: tuple[tuple[str, int], ...] = ()
    without_trip_id: int = 0
    without_sequence: int = 0
    stop_id_only: int = 0
    coverage: dict = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return len(self.keys) + self.without_trip_id + self.without_sequence


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

    @property
    def keys(self) -> Counter[tuple[str, int]]:
        return Counter(k for p in self.measured for k in p.keys)

    def total(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.measured)

    def busiest(self) -> Poll | None:
        """The measured poll with the most stop_time_updates: the one whose
        coverage is recorded, since one poll is what the re-probe records."""
        return max(self.measured, key=lambda p: p.coverage.get("stop_time_updates", 0), default=None)


@dataclass(frozen=True)
class JoinFacts:
    """How the probe's StopTimeUpdates meet the timetable, counted per row
    across every poll as ingest would store them."""

    rows: int
    joined: int
    without_trip_id: int
    without_sequence: int
    stop_id_only: int
    trip_ids: int
    trip_ids_matched: int
    unmatched_sample: tuple[tuple[str, int], ...]
    aomori_pattern: int

    @property
    def rate(self) -> float:
        return self.joined / self.rows if self.rows else 0.0

    @property
    def trip_id_rate(self) -> float:
        return self.trip_ids_matched / self.trip_ids if self.trip_ids else 0.0


@dataclass(frozen=True)
class Decision:
    """A verdict and the facts behind it; an accepted one also says what is
    written.

    ``file`` names the zip member a static verdict is about, ``detail`` is
    the underlying error text, ``agencies`` lists agency.txt's names for
    ``NAME_REQUIRED``, ``holder`` is ``(agency_id, disabled)`` for
    ``FEED_TAKEN``, and ``join`` is set from the join check onwards.
    """

    verdict: Verdict
    name: str | None = None
    timezone: str | None = None
    record_coverage: bool = False
    file: str | None = None
    detail: str | None = None
    agencies: tuple[str, ...] = ()
    holder: tuple[int, bool] | None = None
    join: JoinFacts | None = None

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
    step the probe never reached; ``zip_path`` is the measured zip, in the
    caller's staging directory."""

    feed_url: str
    static_url: str
    decision: Decision
    static: StaticSummary | None = None
    rt: RtProbe | None = None
    zip_path: Path | None = None


@contextmanager
def _reading(name: str) -> Iterator[None]:
    """Turn a decode failure while reading member *name* into a
    StaticZipError naming it."""
    try:
        yield
    except UnicodeDecodeError as exc:
        detail = f"is not UTF-8 ({exc.reason}), and the timetable loader reads UTF-8"
        raise StaticZipError(Verdict.STATIC_UNREADABLE, name, detail) from None
    except csv.Error as exc:
        raise StaticZipError(Verdict.STATIC_UNREADABLE, name, f"is not readable CSV ({exc})") from None


def _agency_rows(zf: zipfile.ZipFile) -> Iterator[dict[str, str]]:
    with zf.open("agency.txt") as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def _stop_sequence(raw: str | None) -> int | None:
    """*raw* as ``static_stop_times.stop_sequence`` stores it: Postgres casts
    the text, surrounding blanks allowed, as Python's ``int`` does."""
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def read_static_summary(zip_path: Path, wanted: Iterable[tuple[str, int]] = ()) -> StaticSummary:
    """The agencies, the required files' row counts, trip_ids, stop_ids and
    those of the *wanted* ``(trip_id, stop_sequence)`` keys ``stop_times.txt``
    holds. Every row is read through ``static_loader.iter_static_rows``, so a
    value matches here exactly when it matches the loaded table.

    Raises :class:`StaticZipError` for a file that is not a zip, a member
    that would decompress past ``MAX_MEMBER_BYTES``, and a member that is not
    UTF-8 CSV.
    """
    wanted_keys = frozenset(wanted)
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise StaticZipError(Verdict.STATIC_UNAVAILABLE, None, "the static URL sent a file that is not a zip") from None
    with zf:
        for info in zf.infolist():
            if info.file_size > MAX_MEMBER_BYTES:
                raise StaticZipError(
                    Verdict.STATIC_UNAVAILABLE,
                    info.filename,
                    f"decompresses to {info.file_size} bytes, over the {MAX_MEMBER_BYTES}-byte limit",
                )
        names = set(zf.namelist())
        agencies = None
        if "agency.txt" in names:
            with _reading("agency.txt"):
                agencies = tuple(
                    StaticAgency((r.get("agency_name") or "").strip(), (r.get("agency_timezone") or "").strip() or None)
                    for r in _agency_rows(zf)
                )
        rows: dict[str, int | None] = dict.fromkeys(REQUIRED_FILES)
        trip_ids: set[str] = set()
        stop_ids: set[str] = set()
        keys: set[tuple[str, int]] = set()
        for name in REQUIRED_FILES:
            if name not in names:
                continue
            n = 0
            with _reading(name):
                for row in iter_static_rows(zf, name):
                    n += 1
                    if name == "trips.txt" and row[0]:
                        trip_ids.add(row[0])
                    elif name == "stops.txt" and row[0]:
                        stop_ids.add(row[0])
                    elif name == "stop_times.txt" and wanted_keys and row[0]:
                        seq = _stop_sequence(row[1])
                        if seq is not None and (row[0], seq) in wanted_keys:
                            keys.add((row[0], seq))
            rows[name] = n
    return StaticSummary(agencies, rows, frozenset(trip_ids), frozenset(stop_ids), frozenset(keys))


def measure_poll(raw: bytes) -> Poll:
    """Decode one poll the way ``static_join.parse_feed`` does. Bytes that are
    not a GTFS-RT FeedMessage come back as a ``not_a_feed`` error rather than
    as an empty feed, which they would otherwise decode to."""
    if not is_feed_message(raw):
        return Poll(error=f"{len(raw)} bytes that are not a GTFS-RT FeedMessage", not_a_feed=True)
    entities = [_fields(e) for e in _fields(raw).get(2, [])]
    without_trip_id = trip_updates_without_trip_id = 0
    for ent in entities:
        if _ENTITY_TRIP_UPDATE not in ent:
            continue
        tu = _fields(ent[_ENTITY_TRIP_UPDATE][0])
        trip = _fields(tu[1][0]) if 1 in tu else {}
        if not (_dec(trip[1][0]) if 1 in trip else None):
            trip_updates_without_trip_id += 1
            without_trip_id += max(1, len(tu.get(2, [])))
    decoded = list(decode_trip_updates(raw))
    keys: list[tuple[str, int]] = []
    without_sequence = stop_id_only = 0
    for tu in decoded:
        for u in tu.stop_time_updates:
            if u.stop_sequence is not None:
                keys.append((tu.trip_id, u.stop_sequence))
            else:
                without_sequence += 1
                stop_id_only += bool(u.stop_id)
    return Poll(
        entities=len(entities),
        trip_updates=sum(1 for e in entities if _ENTITY_TRIP_UPDATE in e),
        vehicles=sum(1 for e in entities if _ENTITY_VEHICLE in e),
        alerts=sum(1 for e in entities if _ENTITY_ALERT in e),
        trip_updates_without_trip_id=trip_updates_without_trip_id,
        trip_ids=frozenset(tu.trip_id for tu in decoded),
        stop_ids=frozenset(u.stop_id for tu in decoded for u in tu.stop_time_updates if u.stop_id),
        keys=tuple(keys),
        without_trip_id=without_trip_id,
        without_sequence=without_sequence,
        stop_id_only=stop_id_only,
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


def _valid_timezone(tz: str | None) -> str | None:
    if not tz:
        return None
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return tz


def static_refusal(static: StaticSummary, name: str | None) -> Decision | None:
    """The refusal a static zip alone earns, or None when ingest could use it
    and the agency can be named."""
    for file in REQUIRED_FILES:
        if not static.rows.get(file):
            state = "missing" if static.rows.get(file) is None else "empty"
            return Decision(Verdict.STATIC_INCOMPLETE, file=file, detail=state)
    if name:
        return None
    if static.agencies is None:
        return Decision(Verdict.NAME_REQUIRED, file="agency.txt", detail="missing")
    if len(static.agencies) != 1 or not static.agencies[0].name:
        return Decision(Verdict.NAME_REQUIRED, file="agency.txt", agencies=tuple(a.name for a in static.agencies))
    return None


def _join_facts(static: StaticSummary, rt: RtProbe) -> JoinFacts:
    keys = rt.keys
    joined = sum(n for k, n in keys.items() if k in static.stop_time_keys and k[0] in static.trip_ids)
    misses = sorted(k for k in keys if not (k in static.stop_time_keys and k[0] in static.trip_ids))
    missed_trips = {t for t, _seq in misses}
    trip_ids = rt.trip_ids
    return JoinFacts(
        rows=rt.total("rows"),
        joined=joined,
        without_trip_id=rt.total("without_trip_id"),
        without_sequence=rt.total("without_sequence"),
        stop_id_only=rt.total("stop_id_only"),
        trip_ids=len(trip_ids),
        trip_ids_matched=len(trip_ids & static.trip_ids),
        unmatched_sample=tuple(misses[:UNMATCHED_SAMPLE]),
        aomori_pattern=sum(1 for t in missed_trips if aomori_regex.parse_trip_id(t) is not None),
    )


def decide(static: StaticSummary, rt: RtProbe, *, name: str | None, accept_empty: bool = False) -> Decision:
    """Whether ``static_join``/``direct_url`` can ingest this feed, and why not."""
    refused = static_refusal(static, name)
    if refused is not None:
        return refused
    only = static.agencies[0] if static.agencies and len(static.agencies) == 1 else None
    agency_name = name or (only.name if only else None)
    timezone = _valid_timezone(only.timezone) if only else None

    def named(verdict: Verdict, **facts: Any) -> Decision:
        return Decision(verdict, name=agency_name, timezone=timezone, **facts)

    if not rt.measured:
        last = rt.polls[-1].error if rt.polls else None
        if rt.polls and all(p.not_a_feed for p in rt.polls):
            return named(Verdict.NOT_A_FEED, detail=last)
        return named(Verdict.FEED_UNREACHABLE, detail=last)
    if rt.total("entities") == 0:
        return named(Verdict.ACCEPT_EMPTY if accept_empty else Verdict.EMPTY)
    if rt.total("trip_updates") == 0:
        return named(Verdict.NO_TRIP_UPDATES)
    if not rt.trip_ids:
        return named(Verdict.NO_TRIP_IDS)
    join = _join_facts(static, rt)
    if join.rows == 0:
        return named(Verdict.NO_STOP_TIME_UPDATES, join=join)
    if join.joined < JOIN_RATE_MIN * join.rows:
        misses = join.rows - join.joined
        stop_id_led = join.stop_id_only * 2 > misses
        return named(Verdict.STOP_ID_ONLY if stop_id_led else Verdict.LOW_JOIN, join=join)
    busiest = rt.busiest()
    sample = busiest.coverage.get("stop_time_updates", 0) if busiest else 0
    return named(Verdict.ACCEPT, join=join, record_coverage=sample >= rt_coverage_reprobe.MIN_SAMPLE)


def probe(
    feed_url: str,
    static_url: str,
    staging: Path,
    *,
    name: str | None = None,
    seconds: float = DEFAULT_PROBE_SECONDS,
    interval: float = DEFAULT_INTERVAL_SEC,
    accept_empty: bool = False,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ProbeOutcome:
    """Validate, fetch the static zip into *staging*, poll the feed and
    decide. Writes nothing outside *staging*, which the caller owns. A static
    zip that settles the refusal on its own ends the probe before the feed is
    polled."""

    def outcome(decision: Decision, **evidence: Any) -> ProbeOutcome:
        return ProbeOutcome(feed_url, static_url, decision, **evidence)

    for label, url in (("--feed-url", feed_url), ("--static-url", static_url)):
        try:
            validate_feed_url(url)
        except FeedURLError as exc:
            return outcome(Decision(Verdict.URL_REJECTED, file=label, detail=str(exc)))
    try:
        zip_path = direct_url.fetch(0, static_url, staging, force=True, label="probe")
    except StaticFetchError as exc:
        return outcome(Decision(Verdict.STATIC_UNAVAILABLE, detail=str(exc)))
    if zip_path is None:
        return outcome(Decision(Verdict.STATIC_UNAVAILABLE, detail="the static URL sent no zip"))
    try:
        static = read_static_summary(zip_path)
        refused = static_refusal(static, name)
        if refused is not None:
            return outcome(refused, static=static, zip_path=zip_path)
        rt = poll_feed(feed_url, seconds=seconds, interval=interval, fetch=fetch, sleep=sleep)
        static = replace(static, stop_time_keys=read_static_summary(zip_path, rt.keys).stop_time_keys)
    except StaticZipError as exc:
        return outcome(Decision(exc.verdict, file=exc.file, detail=exc.detail), zip_path=zip_path)
    return outcome(decide(static, rt, name=name, accept_empty=accept_empty), static=static, rt=rt, zip_path=zip_path)


_INSERT_AGENCY_SQL = """
    INSERT INTO agencies (agency_name, feed_url, static_url, ingest_strategy, static_strategy)
    VALUES ($1, $2, $3, $4, $5)
    RETURNING agency_id
"""
_INSERT_AGENCY_WITH_TIMEZONE_SQL = """
    INSERT INTO agencies (agency_name, feed_url, static_url, ingest_strategy, static_strategy, timezone)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING agency_id
"""


async def add_probed_agency(conn, o: ProbeOutcome) -> int:
    """Insert the agency an accepted probe describes, with its strategies, its
    agency.txt timezone when valid and, when the decision says so, its
    coverage verdict, in one transaction on the asyncpg *conn*. Returns the
    new agency_id.

    Raises ``ValueError`` for a refused probe, which writes nothing. The
    timetable is loaded afterwards: ``load_static`` commits on its own and
    writes ClickHouse, so it cannot share this transaction.
    """
    d = o.decision
    if not d.accepted:
        raise ValueError(f"the probe refused this feed ({d.verdict.value})")
    args: list[Any] = [d.name, o.feed_url, o.static_url, d.ingest_strategy, d.static_strategy]
    busiest = o.rt.busiest() if o.rt is not None else None
    async with conn.transaction():
        if d.timezone:
            agency_id = await conn.fetchval(_INSERT_AGENCY_WITH_TIMEZONE_SQL, *args, d.timezone)
        else:
            agency_id = await conn.fetchval(_INSERT_AGENCY_SQL, *args)
        if d.record_coverage and busiest is not None:
            await record_field_coverage_probe(conn, agency_id, busiest.coverage, o.feed_url)
    return agency_id


@dataclass(frozen=True)
class Onboarding:
    """What :func:`onboard` did: the probe, the agency it added, and why its
    timetable did not load when it did not."""

    outcome: ProbeOutcome
    agency_id: int | None = None
    load_error: str | None = None


def feed_url_holder(conn, feed_url: str) -> tuple[int, bool] | None:
    """``(agency_id, disabled)`` of the agency already on *feed_url*, if any.
    ``UNIQUE (feed_url)`` covers disabled agencies too."""
    with conn.cursor() as cur:
        cur.execute("SELECT agency_id, deleted_at IS NOT NULL FROM agencies WHERE feed_url = %s", (feed_url,))
        row = cur.fetchone()
    conn.rollback()
    return (row[0], row[1]) if row else None


def onboard(
    conn,
    db_url: str,
    feed_url: str,
    static_url: str,
    *,
    dest_dir: Path,
    ch_client: Callable[[], Any],
    dry_run: bool = False,
    name: str | None = None,
    seconds: float = DEFAULT_PROBE_SECONDS,
    interval: float = DEFAULT_INTERVAL_SEC,
    accept_empty: bool = False,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Onboarding:
    """Probe the feed and, unless *dry_run* or refused, add the agency and
    load the timetable the probe measured.

    *conn* is a psycopg2 connection, *db_url* the database the asyncpg insert
    opens, *ch_client* builds the ClickHouse client the load needs. The zip is
    staged in a temporary directory and moved under *dest_dir* only once the
    agency exists, so a dry run or a refusal leaves nothing there. When the
    load fails the agency stays, ``load_error`` says why, and ingest refuses
    it until a ``refresh-static`` loads one.
    """
    import asyncpg

    from pipeline.static_fetcher import load_fetched_recorded

    def taken() -> Onboarding | None:
        holder = feed_url_holder(conn, feed_url)
        if holder is None:
            return None
        return Onboarding(ProbeOutcome(feed_url, static_url, Decision(Verdict.FEED_TAKEN, holder=holder)))

    if (refused := taken()) is not None:
        return refused
    with tempfile.TemporaryDirectory(prefix="feed_probe_") as staging:
        outcome = probe(
            feed_url,
            static_url,
            Path(staging),
            name=name,
            seconds=seconds,
            interval=interval,
            accept_empty=accept_empty,
            fetch=fetch,
            sleep=sleep,
        )
        if dry_run or not outcome.decision.accepted or outcome.zip_path is None:
            return Onboarding(outcome)

        async def insert() -> int:
            aconn = await asyncpg.connect(db_url)
            try:
                return await add_probed_agency(aconn, outcome)
            finally:
                await aconn.close()

        try:
            agency_id = asyncio.run(insert())
        except asyncpg.UniqueViolationError:
            return taken() or Onboarding(outcome)
        try:
            zip_path = direct_url.adopt_staged(outcome.zip_path, agency_id, dest_dir)
            load_fetched_recorded(agency_id, conn, dest_dir, zip_path, STATIC_STRATEGY, ch_client=ch_client())
        except Exception as exc:
            conn.rollback()
            return Onboarding(outcome, agency_id, load_error=str(exc))
    return Onboarding(outcome, agency_id)


def _pct(rate: float) -> str:
    return f"{100 * rate:.0f}%"


def _misses(join: JoinFacts) -> str:
    return ", ".join(f"{t} (stop_sequence {s})" for t, s in join.unmatched_sample) or "none with a stop_sequence"


def reason(o: ProbeOutcome) -> str:
    """The decision, stated for a person."""
    d, rt, j = o.decision, o.rt, o.decision.join
    polls = len(rt.measured) if rt else 0
    match d.verdict:
        case Verdict.FEED_TAKEN:
            agency_id, disabled = d.holder or (0, False)
            return f"agency {agency_id}{' (disabled)' if disabled else ''} already has this feed URL"
        case Verdict.URL_REJECTED:
            return f"{d.file}: {d.detail}"
        case Verdict.STATIC_UNAVAILABLE:
            where = f"{d.file} " if d.file else ""
            return f"the static zip cannot be used: {where}{d.detail}"
        case Verdict.STATIC_UNREADABLE:
            return f"the static zip's {d.file} {d.detail}"
        case Verdict.STATIC_INCOMPLETE:
            needed = ", ".join(REQUIRED_FILES)
            return f"the static zip's {d.file} is {d.detail}, and static_join ingest needs rows in {needed}"
        case Verdict.NAME_REQUIRED:
            if d.detail == "missing":
                return "the static zip has no agency.txt to name the agency; pass --name"
            listed = ", ".join(n or "(unnamed)" for n in d.agencies)
            return f"agency.txt lists {len(d.agencies)} agencies ({listed}) and names none alone; pass --name"
        case Verdict.NOT_A_FEED:
            return f"no poll was a GTFS-RT FeedMessage ({d.detail}); check the feed URL"
        case Verdict.FEED_UNREACHABLE:
            return f"no poll could be read; last: {d.detail}"
        case Verdict.EMPTY:
            return (
                f"the feed sent no entities in {polls} polls; probe again in service hours, "
                "or pass --accept-empty to add it now"
            )
        case Verdict.ACCEPT_EMPTY:
            return (
                f"the feed sent no entities in {polls} polls; added with {INGEST_STRATEGY} as asked, and the "
                "hourly re-probe records its coverage verdict once the feed is in service"
            )
        case Verdict.NO_TRIP_UPDATES:
            assert rt is not None
            return (
                f"the feed sent {rt.total('vehicles')} VehiclePositions and {rt.total('alerts')} Alerts but no "
                f"TripUpdates in {polls} polls; {INGEST_STRATEGY} ingests TripUpdates, so point --feed-url at "
                "the TripUpdates feed"
            )
        case Verdict.NO_TRIP_IDS:
            assert rt is not None
            return (
                f"none of the feed's {rt.total('trip_updates')} TripUpdates names a trip_id, and "
                f"{INGEST_STRATEGY} joins the timetable on trip_id"
            )
        case Verdict.NO_STOP_TIME_UPDATES:
            return (
                "the feed's TripUpdates carry no StopTimeUpdates, and static_join stores one row per "
                "StopTimeUpdate, so there is nothing to join"
            )
    assert j is not None
    joined = (
        f"{_pct(j.rate)} of {j.rows} StopTimeUpdates join stop_times.txt on (trip_id, stop_sequence); "
        f"{j.trip_ids_matched} of {j.trip_ids} trip_ids ({_pct(j.trip_id_rate)}) are in trips.txt"
    )
    if d.verdict is Verdict.STOP_ID_ONLY:
        return (
            f"{joined}. {j.stop_id_only} StopTimeUpdates carry stop_id but no stop_sequence; "
            f"{INGEST_STRATEGY} can't join them"
        )
    if d.verdict is Verdict.LOW_JOIN:
        if j.trip_id_rate >= JOIN_RATE_MIN and j.without_trip_id + j.without_sequence < j.rows - j.joined:
            hint = "the trip_ids match, so the feed numbers stop_sequence differently from stop_times.txt"
        elif j.aomori_pattern:
            hint = (
                f"{j.aomori_pattern} unmatched trip_ids match aomori_regex's trip_id pattern, "
                "so the feed may need that strategy"
            )
        elif j.without_trip_id * 2 > j.rows:
            hint = "most StopTimeUpdates belong to TripUpdates that name no trip_id"
        else:
            hint = "the feed may need a trip_id pattern (aomori_regex) or a different static zip"
        return (
            f"{joined}, below the {JOIN_RATE_MIN:.0%} needed ({j.without_trip_id} without a trip_id, "
            f"{j.without_sequence} without a stop_sequence); sample misses: {_misses(j)}; {hint}"
        )
    coverage = (
        "its coverage verdict is recorded from the busiest poll"
        if d.record_coverage
        else f"the busiest poll has under the {rt_coverage_reprobe.MIN_SAMPLE} stop_time_updates a coverage "
        "verdict needs, so the hourly re-probe records it"
    )
    return f"{joined}; {coverage}"


def report_lines(o: ProbeOutcome) -> list[str]:
    """The probe report a person reads. URLs are redacted: either can carry
    an API key."""
    lines = [f"feed:   {redact_url(o.feed_url)}", f"static: {redact_url(o.static_url)}"]
    if o.static is not None:
        s = o.static
        agencies = (
            "missing"
            if s.agencies is None
            else "; ".join(f"{a.name or '(unnamed)'} ({a.timezone or 'no timezone'})" for a in s.agencies)
        )
        lines.append(f"  agency.txt: {agencies}")
        for file in REQUIRED_FILES:
            n = s.rows.get(file)
            lines.append(f"  {file}: {'missing' if n is None else f'{n} rows'}")
    if o.rt is not None:
        lines.extend(_rt_lines(o.rt, o.static))
    d = o.decision
    lines.append(f"verdict: {d.verdict.value}: {reason(o)}")
    if d.accepted:
        lines.append(
            f"  adds {d.name!r} with ingest_strategy={d.ingest_strategy}, static_strategy={d.static_strategy}, "
            f"timezone={d.timezone or 'the default'}"
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
            f"{p.alerts} Alerts), {p.rows} StopTimeUpdates"
        )
    if static is not None and rt.stop_ids:
        hit, n = len(rt.stop_ids & static.stop_ids), len(rt.stop_ids)
        yield f"  stop_id: {hit} of {n} distinct stop_ids in stops.txt ({_pct(hit / n)})"
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
