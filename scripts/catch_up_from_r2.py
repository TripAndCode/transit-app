"""Catch dev up from the R2 archive, each day joined to the timetable in force on it.

Per agency (every active one, or each --agency-id):

1. The first JST day still to write (the open day) follows from the newest
   row in `updates`, read index-served and bounded below today's JST midnight.
   The collector names an archive by the UTC day it polled, so archive D holds
   JST day D from 09:00 and JST day D+1 until 09:00, and a row's file name
   starts with its archive's day. Ingest writes closed days only, so when the
   newest row, on JST day N, came from archive N, N is complete and N+1 is
   open; when it came from archive N-1's tail, archive N has not been read and
   N itself is open.
2. R2's `rt/<id>/YYYYMMDD.tar.gz` archives from the day before the open day
   through yesterday (JST) are listed: the first of them is read again for its
   tail. Archive members are skipped by name once ingested, so a second read
   writes only what the first left out.
3. The loaded timetable is `static_trips.static_version_id`, the stem of the
   zip `load_static` last loaded. `agg_static_version_summary` is not used: only
   analyze writes it, so after a load it still names the previous version.
   R2's `static/<id>/gtfs_static_YYYYMMDD.zip` dated after it are the newer
   timetables.
4. The plan walks JST days in order. A timetable named D is in force from JST
   day D: the collector stores a zip only when the source's current timetable
   changed, fetching at 09:00 JST, so zip D was already current at the first
   poll of archive D, and its producer dates it D or D-1. The day of collection
   dates it, not calendar.txt: that starts on the first day of the producer's
   rolling validity window, which a revision keeps, so it would date a revision
   on or before the timetable it replaced; reading it would also need every zip
   downloaded before planning.
   The days before D are ingested, then D loads, then the days from D on. The
   split is at JST midnight, not at an archive boundary: archive D-1 is
   ingested once with its JST D members held back (`ingest`'s `now`), and again
   after the load for them, so the morning of D joins D. Midnight is where
   ingest can hold members back, not the 04:00 service-day cutover, so the
   after-midnight end of service day D-1 joins D too.
   A timetable in force only on days already written is skipped. Timetables
   newer than the last archive load at the end, so the newest stays loaded.

Agency 1's timetable reaches R2 only by hand: the collector has no static
source for it, and `aomori_index_scrape` names its zips by the fetching host's
local date. Its ingest reads no timetable (its rows take their schedule from
the trip_id), so the order never changes its rows.

The plan refuses an agency, and --apply runs nothing, when:
- its ingest reads the timetable (REQUIRES_STATIC_TIMETABLE in
  pipeline/strategies/__init__.py) and none is loaded;
- it has no rows: this tool continues a history, it does not start one;
- its loaded timetable came into force after the open day, or
  carries no date to order R2's against, so those days would join the wrong one;
- R2 lists one day twice;
- a local copy of a file it needs differs in size from R2's.
An archive missing in R2 stops the agency's plan at that day, with the
timetables in force from it, because going past it would leave it older than
the newest ingested day for good: --allow-gaps goes past every missing day.

The plan (the default) lists R2 and reads Postgres through a read-only session
and ClickHouse through a readonly=1 client; it downloads and writes nothing.
--apply plans again under the pipeline's ingest/analyze lock (pipeline.locks),
held for the whole run, and then runs the plan. Each ingest step first checks
that the open day has not moved past its first day, so no day is written out of
order or twice. Files download into the local layout `make fetch` uses
(raw_archives/<id>/, raw_archives_static/<id>/) and each ingest step reads its
own temporary directory of links to just its archives, never the whole local
history. It stops at the first failure. A rerun is safe: ingested archive
members are skipped, and a loaded timetable is not loaded again.

Aggregates are left to the next analyze, which the run prints, or --analyze
runs in-process afterwards (incremental, like analyze_all): a timetable load
changes the agency's static fingerprint, so analyze rebuilds every date of an
agency that loaded one. Analyzing once at the end rather than after each load
pays that rebuild once per agency; the rows themselves keep the schedule each
was joined to.

Usage:
    poetry run python scripts/catch_up_from_r2.py [--agency-id ID ...]
    poetry run python scripts/catch_up_from_r2.py --apply [--analyze] [--agency-id ID ...]
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import re
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clickhouse_connect  # noqa: E402  (sys.path injection above)
import psycopg2  # noqa: E402

from pipeline.clickhouse import ch_conn_kwargs, jst_date, jst_midnight_utc  # noqa: E402
from pipeline.db import _static_loaded  # noqa: E402
from pipeline.strategies import get_ingest_strategy  # noqa: E402

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
_DAY = timedelta(days=1)
_ARCHIVE_NAME = re.compile(r"([0-9]{8})\.tar\.gz")
_TIMETABLE_NAME = re.compile(r"gtfs_static_([0-9]{8})\.zip")
_VERSION = re.compile(r"gtfs_static_([0-9]{8})")
# The poll's UTC day in a stored file name: `YYYYMMDD/...` from an archive,
# `oracle/YYYYMMDD/...` or `live_YYYYMMDDT...` from the live path.
_POLL_DAY = re.compile(r"[0-9]{8}")
_STORE_VARS = (
    "OBJECT_STORE_ENDPOINT",
    "OBJECT_STORE_BUCKET",
    "OBJECT_STORE_ACCESS_KEY_ID",
    "OBJECT_STORE_SECRET_ACCESS_KEY",
)


class Refusal(Exception):
    """A run that must not go ahead, with what the operator should do."""


# ── planning (pure) ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RemoteFile:
    key: str
    name: str
    day: date
    size: int


@dataclass(frozen=True)
class AgencyState:
    agency_id: int
    name: str
    requires_timetable: bool
    has_timetable: bool
    loaded_version: str | None
    newest_row: datetime | None
    newest_file: str | None = None

    @property
    def open_day(self) -> date | None:
        """The first JST day still to write (module docstring, rule 1)."""
        return open_day(self.newest_row, self.newest_file)


@dataclass(frozen=True)
class IngestStep:
    """Write JST days from ``first_day`` up to ``cutoff`` (exclusive; None is
    the day the run happens) from ``archives``."""

    first_day: date
    cutoff: date | None
    archives: tuple[RemoteFile, ...]

    def last_day(self, today: date) -> date:
        return (self.cutoff or today) - _DAY


@dataclass(frozen=True)
class LoadStep:
    timetable: RemoteFile


Step = IngestStep | LoadStep


@dataclass
class AgencyPlan:
    state: AgencyState
    steps: list[Step] = field(default_factory=list)
    gaps: list[date] = field(default_factory=list)
    stopped_at: date | None = None
    deferred: list[RemoteFile] = field(default_factory=list)
    superseded: list[RemoteFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refusal: str | None = None

    @property
    def archives(self) -> list[RemoteFile]:
        seen: dict[str, RemoteFile] = {}
        for step in self.steps:
            if isinstance(step, IngestStep):
                seen.update((a.key, a) for a in step.archives)
        return list(seen.values())

    @property
    def timetables(self) -> list[RemoteFile]:
        return [step.timetable for step in self.steps if isinstance(step, LoadStep)]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in [*self.archives, *self.timetables])


def _dated(listing: Iterable[tuple[str, int]], pattern: re.Pattern) -> tuple[list[RemoteFile], list[str]]:
    files: list[RemoteFile] = []
    ignored: list[str] = []
    for key, size in listing:
        name = key.rsplit("/", 1)[-1]
        match = pattern.fullmatch(name)
        try:
            day = datetime.strptime(match[1], "%Y%m%d").date() if match else None
        except ValueError:
            day = None
        if day is None:
            ignored.append(name)
        else:
            files.append(RemoteFile(key, name, day, size))
    return sorted(files, key=lambda f: (f.day, f.name)), ignored


def archive_files(listing: Iterable[tuple[str, int]]) -> tuple[list[RemoteFile], list[str]]:
    """The day archives among ``(key, size)`` listings, by day, and the names
    that are not one."""
    return _dated(listing, _ARCHIVE_NAME)


def timetable_files(listing: Iterable[tuple[str, int]]) -> tuple[list[RemoteFile], list[str]]:
    """The dated timetable zips among ``(key, size)`` listings, by day, and the
    names that are not one."""
    return _dated(listing, _TIMETABLE_NAME)


def version_day(version: str | None) -> date | None:
    """The day a loaded ``static_version_id`` names, or None for one that names
    none (a legacy stem such as ``hiroden_static``)."""
    match = _VERSION.fullmatch(version or "")
    if match is None:
        return None
    try:
        return datetime.strptime(match[1], "%Y%m%d").date()
    except ValueError:
        return None


def _poll_day(file_name: str | None) -> date | None:
    match = _POLL_DAY.search(file_name or "")
    if match is None:
        return None
    try:
        return datetime.strptime(match[0], "%Y%m%d").date()
    except ValueError:
        return None


def open_day(newest_row: datetime | None, newest_file: str | None) -> date | None:
    """The first JST day still to write, given the newest row in `updates` and
    its file name, or None when there is no row. That row's JST day is still
    open only when it came from the previous day's archive."""
    if newest_row is None:
        return None
    newest = jst_date(newest_row)
    return newest if _poll_day(newest_file) == newest - _DAY else newest + _DAY


def _in_force(timetables: Sequence[RemoteFile], day: date) -> RemoteFile | None:
    dated = [t for t in timetables if t.day <= day]
    return dated[-1] if dated else None


def _in_force_hint(timetables: Sequence[RemoteFile], day: date) -> str:
    found = _in_force(timetables, day)
    if found is None:
        return f"; R2 holds no timetable dated on or before {day}"
    return f"; R2's in force on {day} is {found.key}"


def _add_ingest(plan: AgencyPlan, start: date, cutoff: date | None, archives: list[RemoteFile], today: date) -> None:
    last = (cutoff or today) - _DAY
    if last < start:
        return
    chosen = tuple(a for a in archives if start - _DAY <= a.day <= last)
    if chosen:
        plan.steps.append(IngestStep(start, cutoff, chosen))


def plan_agency(
    state: AgencyState,
    archives: Sequence[RemoteFile],
    timetables: Sequence[RemoteFile],
    *,
    today: date,
    allow_gaps: bool = False,
) -> AgencyPlan:
    """The steps that catch ``state`` up from R2's ``archives`` and
    ``timetables`` (each sorted by day), as of JST ``today``. The module
    docstring states the rules; a refused plan has no steps."""
    plan = AgencyPlan(state)
    first = state.open_day

    if state.requires_timetable and not state.has_timetable:
        hint = _in_force_hint(timetables, first) if first else ""
        plan.refusal = (
            "its ingest joins every row to the loaded timetable (REQUIRES_STATIC_TIMETABLE) and none is loaded. "
            f"Load the one in force on the first day to write{hint}, with gtfs_pipeline.py load_static, then rerun"
        )
        return plan
    if first is None:
        plan.refusal = (
            "`updates` holds no rows for it. This tool continues an agency's history; ingest its first days by hand, "
            "with the timetable in force then loaded"
        )
        return plan
    days = [a.day for a in archives]
    twice = sorted({d for d in days if days.count(d) > 1})
    if twice:
        plan.refusal = f"R2 lists more than one archive for {', '.join(map(str, twice))}; a day is ingested once"
        return plan
    loaded_day = version_day(state.loaded_version) if state.has_timetable else None
    if state.has_timetable and loaded_day is None and timetables:
        plan.refusal = (
            f"the loaded timetable {state.loaded_version!r} carries no gtfs_static_YYYYMMDD date, so R2's cannot be "
            f"ordered against it. Load the one in force on {first} by hand{_in_force_hint(timetables, first)}, "
            "then rerun"
        )
        return plan
    if loaded_day is not None and loaded_day > first:
        if state.requires_timetable:
            plan.refusal = (
                f"{state.loaded_version} is loaded, in force from {loaded_day}, but JST {first}..{loaded_day - _DAY} "
                f"are not ingested yet and would join it, not the timetable in force on them"
                f"{_in_force_hint(timetables, first)}. Load that one with gtfs_pipeline.py load_static, then rerun"
            )
            return plan
        plan.notes.append(
            f"{state.loaded_version} came into force after {first}; this agency's ingest reads no timetable, "
            "so the order leaves its rows unchanged"
        )

    since = first - _DAY
    wanted = [a for a in archives if since <= a.day <= today - _DAY]
    present = {a.day for a in wanted}
    if present:
        span = (max(present) - since).days + 1
        plan.gaps = [since + _DAY * i for i in range(span) if since + _DAY * i not in present]
    if plan.gaps and not allow_gaps:
        plan.stopped_at = plan.gaps[0]
    stop = plan.stopped_at

    if loaded_day is None:
        newer: list[RemoteFile] = []
        if timetables and not state.has_timetable:
            plan.notes.append("no timetable is loaded and its ingest reads none; R2's are left to load by hand")
    else:
        newer = [t for t in timetables if t.day > loaded_day]
    if stop is not None:
        plan.deferred = [t for t in newer if t.day >= stop]
        newer = [t for t in newer if t.day < stop]
    early = [t for t in newer if t.day <= first]
    plan.superseded = early[:-1]
    loads = early[-1:] + [t for t in newer if t.day > first]

    start = first
    for timetable in loads:
        if timetable.day > start:
            _add_ingest(plan, start, timetable.day, wanted, today)
            start = timetable.day
        plan.steps.append(LoadStep(timetable))
    _add_ingest(plan, start, stop, wanted, today)
    return plan


def check_order(open_day: date | None, first_day: date) -> None:
    """Refuse an ingest step that starts before the first JST day `updates`
    leaves open: it would write a closed day again, or out of order."""
    if open_day is not None and open_day > first_day:
        raise Refusal(
            f"`updates` is written through JST {open_day - _DAY}, and this step writes from {first_day}. Another "
            "ingest ran since the plan was made; rerun to plan again"
        )


# ── object store and local files ──────────────────────────────────────────────


class ObjectStore(Protocol):
    description: str

    def list(self, prefix: str, *, start_after: str | None = None) -> list[tuple[str, int]]:
        """``(key, size)`` of every object under ``prefix``, after
        ``start_after`` when given."""
        ...

    def download(self, key: str, dest: pathlib.Path) -> None: ...


class R2Store:
    """The archive bucket, through the OBJECT_STORE_* credentials. It lists and
    downloads, and has no call that writes."""

    def __init__(self) -> None:
        missing = [name for name in _STORE_VARS if not os.environ.get(name)]
        if missing:
            raise Refusal("not set: " + ", ".join(missing))
        import boto3

        self._client = boto3.client(
            "s3",
            endpoint_url=os.environ["OBJECT_STORE_ENDPOINT"],
            aws_access_key_id=os.environ["OBJECT_STORE_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["OBJECT_STORE_SECRET_ACCESS_KEY"],
        )
        self._bucket = os.environ["OBJECT_STORE_BUCKET"]
        self.description = f"bucket {self._bucket}"

    def list(self, prefix: str, *, start_after: str | None = None) -> list[tuple[str, int]]:
        kwargs = {"Bucket": self._bucket, "Prefix": prefix}
        if start_after:
            kwargs["StartAfter"] = start_after
        found: list[tuple[str, int]] = []
        for page in self._client.get_paginator("list_objects_v2").paginate(**kwargs):
            found += [(obj["Key"], obj["Size"]) for obj in page.get("Contents", [])]
        return found

    def download(self, key: str, dest: pathlib.Path) -> None:
        self._client.download_file(self._bucket, key, str(dest))


@dataclass(frozen=True)
class Layout:
    """Where downloads land: ``<archives>/<id>/YYYYMMDD.tar.gz`` and
    ``<timetables>/<id>/gtfs_static_YYYYMMDD.zip``, the layout `make fetch`
    writes."""

    archives: pathlib.Path
    timetables: pathlib.Path

    def path(self, agency_id: int, f: RemoteFile) -> pathlib.Path:
        root = self.timetables if _TIMETABLE_NAME.fullmatch(f.name) else self.archives
        return root / str(agency_id) / f.name

    def to_download(self, agency_id: int, files: Iterable[RemoteFile]) -> tuple[int, list[str]]:
        """Bytes not yet local, and each local copy whose size differs from R2's."""
        missing = 0
        conflicts = []
        for f in files:
            local = self.path(agency_id, f)
            if not local.exists():
                missing += f.size
            elif local.stat().st_size != f.size:
                conflicts.append(
                    f"{local} is {local.stat().st_size:,} bytes and R2's {f.key} is {f.size:,}; move it aside so the "
                    "R2 copy downloads, then rerun"
                )
        return missing, conflicts

    def fetch(self, store: ObjectStore, agency_id: int, f: RemoteFile) -> pathlib.Path:
        """The local copy of ``f``, downloaded first when absent. A partial
        download is named ``*.part``, which neither ingest's ``*.tar.gz`` nor
        the loader's ``*static*.zip`` pattern matches."""
        dest = self.path(agency_id, f)
        _missing, conflicts = self.to_download(agency_id, [f])
        if conflicts:
            raise Refusal(conflicts[0])
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        store.download(f.key, part)
        got = part.stat().st_size
        if got != f.size:
            part.unlink()
            raise RuntimeError(f"downloaded {got:,} bytes of {f.key}, which R2 lists at {f.size:,}")
        part.replace(dest)
        logger.info(f"  downloaded {f.key} ({_mb(f.size)})")
        return dest


# ── state ─────────────────────────────────────────────────────────────────────


def loaded_version(conn, agency_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT max(static_version_id) FROM static_trips WHERE agency_id = %s", (agency_id,))
        return cur.fetchone()[0]


def newest_row(ch_client, agency_id: int, today: date) -> tuple[datetime | None, str | None]:
    """The agency's newest row before ``today`` (JST): its captured_at and file
    name. `ORDER BY ... LIMIT 1` on the sort key's second column, not max():
    it reads the last granules instead of every row the agency has."""
    rows = ch_client.query(
        "SELECT captured_at, file_name FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND captured_at < {before:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "before": jst_midnight_utc(today)},
    ).result_rows
    if not rows:
        return None, None
    captured_at, file_name = rows[0]
    return (captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=timezone.utc)), file_name


def read_states(conn, ch_client, agency_ids: list[int] | None, today: date) -> list[AgencyState]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, agency_name, ingest_strategy FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
        )
        rows = cur.fetchall()
    if agency_ids:
        unknown = sorted(set(agency_ids) - {r[0] for r in rows})
        if unknown:
            raise Refusal(f"no active agency {', '.join(map(str, unknown))}")
        rows = [r for r in rows if r[0] in agency_ids]
    states = []
    for agency_id, name, strategy in rows:
        captured_at, file_name = newest_row(ch_client, agency_id, today)
        states.append(
            AgencyState(
                agency_id=agency_id,
                name=name,
                requires_timetable=bool(getattr(get_ingest_strategy(strategy), "REQUIRES_STATIC_TIMETABLE", False)),
                has_timetable=_static_loaded(conn, agency_id),
                loaded_version=loaded_version(conn, agency_id),
                newest_row=captured_at,
                newest_file=file_name,
            )
        )
    return states


def plan_all(
    conn,
    ch_client,
    store: ObjectStore,
    layout: Layout,
    agency_ids: list[int] | None,
    today: date,
    allow_gaps: bool,
) -> list[AgencyPlan]:
    plans = []
    for state in read_states(conn, ch_client, agency_ids, today):
        prefix = f"rt/{state.agency_id}/"
        after = f"{prefix}{state.open_day - 2 * _DAY:%Y%m%d}.tar.gz" if state.open_day else None
        archives, odd_archives = archive_files(store.list(prefix, start_after=after))
        timetables, odd_timetables = timetable_files(store.list(f"static/{state.agency_id}/"))
        plan = plan_agency(state, archives, timetables, today=today, allow_gaps=allow_gaps)
        if odd_archives or odd_timetables:
            plan.notes.append(f"R2 names left alone: {', '.join(odd_archives + odd_timetables)}")
        if plan.refusal is None:
            _missing, conflicts = layout.to_download(state.agency_id, [*plan.archives, *plan.timetables])
            if conflicts:
                plan.refusal = "; ".join(conflicts)
        plans.append(plan)
    return plans


# ── report ────────────────────────────────────────────────────────────────────


def _mb(n: int) -> str:
    return f"{n / 1e6:,.1f} MB"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _span(first: date, last: date) -> str:
    return str(first) if first == last else f"{first}..{last}"


def _names(files: Sequence[RemoteFile]) -> str:
    days = [f.day for f in files]
    if days == [days[0] + _DAY * i for i in range(len(days))]:
        return f"{days[0]:%Y%m%d}" if len(days) == 1 else f"{days[0]:%Y%m%d}..{days[-1]:%Y%m%d}"
    return ", ".join(f"{d:%Y%m%d}" for d in days)


def print_plan(plans: list[AgencyPlan], layout: Layout, today: date) -> None:
    total = downloads = n_archives = n_loads = 0
    for plan in plans:
        state = plan.state
        newest = state.newest_row.astimezone(_JST).strftime("%Y-%m-%d %H:%M JST") if state.newest_row else "none"
        timetable = state.loaded_version if state.has_timetable else "none"
        reads = "" if state.requires_timetable else " (its ingest reads none)"
        print(f"\n[a{state.agency_id}] {state.name}: newest row {newest}; timetable {timetable}{reads}")
        for i, step in enumerate(plan.steps, 1):
            if isinstance(step, IngestStep):
                size = sum(a.size for a in step.archives)
                print(
                    f"  {i}. ingest JST {_span(step.first_day, step.last_day(today))} from {_names(step.archives)} "
                    f"({_count(len(step.archives), 'archive')}, {_mb(size)})"
                )
            else:
                t = step.timetable
                print(f"  {i}. load   {t.name} ({_mb(t.size)}), in force from JST {t.day}")
        for gap in plan.gaps:
            print(f"  missing in R2: {gap:%Y%m%d}" + (" (gone past: --allow-gaps)" if plan.stopped_at is None else ""))
        if plan.stopped_at:
            held = f"; held back with it: {', '.join(t.name for t in plan.deferred)}" if plan.deferred else ""
            print(f"  stops before JST {plan.stopped_at}: --allow-gaps goes past a day lost for good{held}")
        if plan.superseded:
            print(f"  skipped, in force only on days already ingested: {', '.join(t.name for t in plan.superseded)}")
        for note in plan.notes:
            print(f"  note: {note}")
        if plan.refusal:
            print(f"  REFUSED: {plan.refusal}")
            continue
        if not plan.steps:
            print("  nothing to do")
            continue
        missing, _conflicts = layout.to_download(state.agency_id, [*plan.archives, *plan.timetables])
        print(
            f"  {_count(len(plan.archives), 'archive')}, {_count(len(plan.timetables), 'timetable')}: "
            f"{_mb(plan.total_bytes)}, "
            f"{_mb(missing)} to download"
        )
        total += plan.total_bytes
        downloads += missing
        n_archives += len(plan.archives)
        n_loads += len(plan.timetables)
    print(
        f"\nall: {_count(n_archives, 'archive')}, {_count(n_loads, 'timetable load')}, {_mb(total)} "
        f"({_mb(downloads)} to download). "
        f"JST {today} is open: archive members on it wait for a later run."
    )


def print_next_steps(plans: list[AgencyPlan], agency_ids: list[int] | None) -> None:
    changed = [p.state.agency_id for p in plans if p.steps and p.refusal is None]
    if not changed:
        return
    reloaded = [p.state.agency_id for p in plans if p.timetables and p.refusal is None]
    print("\nNext, the aggregates (incremental: each agency rebuilds the dates whose rows changed):")
    if agency_ids:
        for agency_id in changed:
            print(f"  make analyze AGENCY_ID={agency_id}")
    else:
        print("  make analyze-all")
    if reloaded:
        ids = ", ".join(map(str, reloaded))
        print(f"Agencies {ids} load a timetable, which changes their static fingerprint: they rebuild every date once.")
    print("--apply --analyze runs it in the same run instead.")


# ── connections ───────────────────────────────────────────────────────────────


def _pg_connect(*, readonly: bool):
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise Refusal("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    # The civil day Postgres-side date casts use, as gtfs_pipeline's commands
    # pin it; set before the session turns read-only.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    conn.autocommit = False
    if readonly:
        conn.set_session(readonly=True)
    return conn


def _targets(store: ObjectStore) -> list[str]:
    kw = ch_conn_kwargs()
    pg = urlsplit(os.environ.get("DATABASE_URL", ""))
    return [
        f"Postgres:     database {pg.path.lstrip('/')} on {pg.hostname}:{pg.port or 5432}",
        f"ClickHouse:   database {kw['database']} on {kw['host']}:{kw['port']}",
        f"Object store: {store.description}",
    ]


# ── apply ─────────────────────────────────────────────────────────────────────


def _confirm(args, database: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("--apply writes the database: pass --yes, or run it from a terminal to confirm", file=sys.stderr)
        return False
    answer = input(f"Type the Postgres database name ({database}) to run this plan against it: ")
    return answer.strip() == database


def _ingest_step(step: IngestStep, agency_id: int, conn, ch_client, store: ObjectStore, layout: Layout, now: datetime):
    from pipeline import runs as pipeline_runs
    from pipeline.ingest import ingest

    check_order(open_day(*newest_row(ch_client, agency_id, jst_date(now))), step.first_day)
    paths = [layout.fetch(store, agency_id, a) for a in step.archives]
    # ingest() leaves every member on a JST day at or after jst_date(now)
    # unmarked, so `now` at the cutoff's JST midnight holds those members back
    # for the step after the timetable load.
    run_now = min(now, jst_midnight_utc(step.cutoff)) if step.cutoff else now
    with tempfile.TemporaryDirectory(prefix=f"catch-up-a{agency_id}-") as stage:
        for path in paths:
            (pathlib.Path(stage) / path.name).symlink_to(path.resolve())
        with pipeline_runs.record_run(conn, "ingest", agency_id=agency_id) as run:
            run.rows = ingest(stage, agency_id, conn, ch_client, now=run_now, strict=True)
    print(f"  ingested JST {_span(step.first_day, step.last_day(jst_date(now)))}: {run.rows:,} rows")


def _load_step(step: LoadStep, agency_id: int, conn, store: ObjectStore, layout: Layout) -> None:
    from pipeline import day_types, route_catalog, service_hours
    from pipeline import runs as pipeline_runs
    from pipeline.static_loader import load_static

    timetable = step.timetable
    current = loaded_version(conn, agency_id)
    conn.commit()
    if current == pathlib.Path(timetable.name).stem:
        print(f"  {timetable.name} is already loaded")
        return
    current_day = version_day(current)
    if current_day is not None and current_day > timetable.day:
        raise Refusal(f"{current} is loaded, newer than {timetable.name}; rerun to plan again")
    path = layout.fetch(store, agency_id, timetable)
    with pipeline_runs.record_run(conn, "static", agency_id=agency_id):
        load_static(str(path), agency_id, conn)
    # Process-wide caches a later --analyze in this run would otherwise read
    # from the timetable just replaced.
    day_types.invalidate_day_types()
    route_catalog.invalidate_retired_routes()
    service_hours.invalidate_service_hours()
    print(f"  loaded {timetable.name}")


def _analyze(conn, agency_ids: list[int]) -> list[int]:
    from pipeline import runs as pipeline_runs
    from pipeline.analyze import analyze
    from pipeline.clickhouse import ANALYZE_SEND_RECEIVE_TIMEOUT_SEC, get_client

    ch_client = get_client(send_receive_timeout=ANALYZE_SEND_RECEIVE_TIMEOUT_SEC)
    failed = []
    for agency_id in agency_ids:
        print(f"analyze agency {agency_id}")
        try:
            with pipeline_runs.record_run(conn, "analyze", agency_id=agency_id):
                analyze(agency_id, conn, ch_client)
        except Exception:
            logger.exception(f"analyze failed for agency {agency_id}")
            failed.append(agency_id)
    return failed


def run_apply(args, store: ObjectStore, layout: Layout, now: datetime) -> int:
    from pipeline.clickhouse import get_client
    from pipeline.locks import try_lock_ingest_analyze

    today = jst_date(now)
    conn = _pg_connect(readonly=False)
    try:
        # Held until `conn` closes, across every step: no other ingest or
        # analyze interleaves with this run's.
        if not try_lock_ingest_analyze(conn):
            raise Refusal("another ingest or analyze job holds the pipeline lock; rerun once it ends")
        ch_client = get_client()
        plans = plan_all(conn, ch_client, store, layout, args.agency_id, today, args.allow_gaps)
        conn.commit()
        print_plan(plans, layout, today)
        refused = [p.state.agency_id for p in plans if p.refusal]
        if refused:
            raise Refusal(
                f"agencies {', '.join(map(str, refused))} cannot be caught up as they are (see above); "
                "settle them or leave them out with --agency-id, then rerun"
            )
        if not any(p.steps for p in plans):
            print("\nnothing to do")
            return 0
        if not _confirm(args, urlsplit(os.environ["DATABASE_URL"]).path.lstrip("/")):
            return 1
        for plan in plans:
            agency_id = plan.state.agency_id
            for i, step in enumerate(plan.steps, 1):
                print(f"[a{agency_id}] step {i}/{len(plan.steps)}")
                try:
                    if isinstance(step, IngestStep):
                        _ingest_step(step, agency_id, conn, ch_client, store, layout, now)
                    else:
                        _load_step(step, agency_id, conn, store, layout)
                except Exception as exc:
                    conn.rollback()
                    print(
                        f"stopped: agency {agency_id} step {i}: {type(exc).__name__}: {exc}\n"
                        "Rerun once it is fixed: ingested archive members are skipped, and a loaded timetable is "
                        "not loaded again.",
                        file=sys.stderr,
                    )
                    return 1
        if args.analyze:
            failed = _analyze(conn, [p.state.agency_id for p in plans])
            if failed:
                print(f"analyze failed for agencies {', '.join(map(str, failed))}", file=sys.stderr)
                return 1
        else:
            print_next_steps(plans, args.agency_id)
        return 0
    finally:
        conn.close()


def run_plan(args, store: ObjectStore, layout: Layout, now: datetime) -> int:
    today = jst_date(now)
    conn = _pg_connect(readonly=True)
    try:
        ch_client = clickhouse_connect.get_client(**ch_conn_kwargs(), settings={"readonly": 1})
        plans = plan_all(conn, ch_client, store, layout, args.agency_id, today, args.allow_gaps)
    finally:
        conn.close()
    print_plan(plans, layout, today)
    print("\nplan only: nothing downloaded or written. --apply runs it.")
    print_next_steps(plans, args.agency_id)
    return 0


def main(argv: list[str] | None = None, *, store: ObjectStore | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--agency-id", type=int, action="append", metavar="ID", help="this agency only (repeatable; default: all)"
    )
    parser.add_argument("--apply", action="store_true", help="download, ingest and load (default: print the plan)")
    parser.add_argument("--yes", action="store_true", help="skip the --apply confirmation prompt")
    parser.add_argument("--analyze", action="store_true", help="after --apply, analyze the agencies in scope")
    parser.add_argument("--allow-gaps", action="store_true", help="go past days R2 has no archive for")
    parser.add_argument("--archives-dir", type=pathlib.Path, default=ROOT / "raw_archives")
    parser.add_argument("--static-dir", type=pathlib.Path, default=ROOT / "raw_archives_static")
    args = parser.parse_args(argv)
    if args.analyze and not args.apply:
        parser.error("--analyze runs after --apply")
    if args.apply:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    now = now or datetime.now(timezone.utc)
    try:
        store = store or R2Store()
        for line in _targets(store):
            print(line + ("" if args.apply else " (read only)"))
        layout = Layout(args.archives_dir, args.static_dir)
        return run_apply(args, store, layout, now) if args.apply else run_plan(args, store, layout, now)
    except Refusal as why:
        print(f"refused: {why}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
