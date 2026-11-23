"""In-repo GTFS-RT collector for a standalone deployment (`gtfs_pipeline.py poll`).

It produces what the Oracle collector (`oracle_cloud/v3/bin/rt-poller.sh`,
`rotate-day.sh`) produces and hands it to the same code the app already runs.

Every cycle, once the agency list has been read (from Postgres, or the last
list read while Postgres is down):
- each enabled agency's feed is fetched, a few at a time and each within a
  wall-clock deadline, so one slow or dead feed delays no other;
- each poll is archived as it arrives, as
  ``<archive>/<agency_id>/rt/<UTC YYYYMMDD>/TripUpdate_HHMMSS.pb``, whatever
  the state of ClickHouse;
- then it is pushed into `updates_live` through
  `pipeline.collector_push.ingest_pushed_poll`, the function the collector's
  HTTP push uses, so it carries the same provenance. The file is in place
  before its push, which keeps every pushed poll in the archive: archive
  replacement counts a pushed poll without its member as a gap. After the
  first push that fails for a reason other than the poll itself, the rest of
  the cycle's polls are archived only.

On a worker thread, so polls keep their pace, the steps
`pipeline.poller_schedule` decides:
- the first cycle of each UTC day packs every closed day directory into
  ``<YYYYMMDD>.tar.gz`` in `rotate-day.sh`'s format, then, under the lock the
  cron sweep takes, promotes the closed JST days, ingests the tarballs not yet
  ingested with `pipeline.ingest.ingest` (where the day promotion stored is
  replaced once both of its tarballs are read), analyzes, and checks that the
  aggregates are fresh;
- the first cycle of each JST day promotes and analyzes, so yesterday reaches
  history at JST midnight rather than at the UTC rollover nine hours later;
- a stage that failed runs again on its own, within its own budget.
With ``$POLLER_PING_URL`` set, each settled step reports to that
healthchecks.io-style check as `pipeline.poller_schedule.heartbeat` decides, so
a step that stops running, or a stage that spends its retries, alerts.
Once an hour another thread re-probes the RT field-coverage verdicts that are
due (`pipeline.rt_coverage_reprobe`, which waits for each agency's service
hours).

Ingest reads every tarball after the agency's ``.ingested-through`` marker,
plus the one before it, since a JST day spans two UTC days. The marker moves
past a tarball only once an ingest has read it with no failure a rerun could
get past (`pipeline.ingest.IngestReport`); a file whose bytes never decode
does not hold it. ``gtfs_pipeline.py ingest <archive>/<agency_id>/rt
--agency-id <id>`` reads every tarball by hand.

Feed URLs come only from the agencies table, and are fetched through
`pipeline.url_guard.safe_urlopen` (http(s) only, every redirect hop vetted,
body capped). A failed fetch, archive write or push is logged and counted, and
the next poll goes ahead. SIGTERM ends the loop once the cycle in flight has
archived what arrived; a fetch still running ends by its deadline, counted
from its start. The tarballs stay in the archive volume; nothing uploads or
prunes them.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import signal
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import AbstractContextManager, ExitStack
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from pipeline import poller_schedule as schedule
from pipeline.collector_push import MAX_POLL_BYTES, UnknownAgency, ingest_pushed_poll, member_name
from pipeline.stores import Stores, open_stores
from pipeline.url_guard import redact_url, redact_urls_in_text, safe_urlopen

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 60.0
FETCH_TIMEOUT_SEC = 15
# The longest one fetch may take end to end, however slowly the body drips.
FETCH_DEADLINE_SEC = 30
FETCH_WORKERS = 4
# A poll cycle's pushes give up on a ClickHouse that stops answering after
# this long, rather than holding the cycle.
POLL_CH_TIMEOUT_SEC = 15
REPROBE_INTERVAL_SEC = 60 * 60
HEARTBEAT_ENV = "POLLER_PING_URL"
HEARTBEAT_TIMEOUT_SEC = 10
INGESTED_MARKER = ".ingested-through"
# Beside the marker: the day a transient failure holds it at, how many UTC
# days' steps have held it there, and the last such day.
HELD_MARKER = ".ingest-held"
# A day held this many UTC days running is passed over, so one file failing
# the same way every time cannot pin the marker and grow the window forever.
MAX_HOLDS = 3

_STAGE_ORDER = ("promote", "ingest", "analyze")
_DAY_DIR = re.compile(r"[0-9]{8}")
_ENABLED_AGENCIES_SQL = (
    "SELECT agency_id, feed_url FROM agencies "
    "WHERE deleted_at IS NULL AND feed_url IS NOT NULL AND feed_url <> '' ORDER BY agency_id"
)

# Each agency's stages to run, or, returned, the stages that failed.
Plan = dict[int, set[str]]
Fetcher = Callable[[str], bytes]
StoresFactory = Callable[[], AbstractContextManager[Stores]]
# Takes the outcome `pipeline.poller_schedule.heartbeat` decided and a body.
Heartbeat = Callable[[str, str], None]
DailyStep = Callable[[Path, Plan], Plan]

OK, SKIPPED, FAILED = "ok", "skipped", "failed"


def configured_interval_sec() -> float:
    """``$POLL_INTERVAL_SEC``, else ``DEFAULT_INTERVAL_SEC``: the pace of the
    poller and of ``add_agency``'s probe of a new feed."""
    return float(os.environ.get("POLL_INTERVAL_SEC") or DEFAULT_INTERVAL_SEC)


def send_heartbeat(url: str, outcome: str, body: str) -> None:
    """POST *body* to the check: its bare URL for ``"ok"``, ``<url>/fail`` for
    ``"fail"``. Best effort, and the URL is never logged: its path is the
    check's credential."""
    target = url.rstrip("/") + ("/fail" if outcome == "fail" else "")
    request = urllib.request.Request(target, data=body.encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=HEARTBEAT_TIMEOUT_SEC):
            pass
    except HTTPError as exc:
        exc.close()
        logger.warning("poller: the heartbeat check answered HTTP %s", exc.code)
    except Exception as exc:
        logger.warning("poller: the heartbeat could not be sent: %s", type(exc).__name__)


def configured_heartbeat() -> Heartbeat | None:
    """``$POLLER_PING_URL`` as a heartbeat, or None when it is unset or not an
    http(s) URL."""
    url = os.environ.get(HEARTBEAT_ENV, "").strip()
    if not url:
        return None
    if not url.startswith(("https://", "http://")):
        logger.error("poller: $%s is not an http(s) URL; no heartbeat is sent", HEARTBEAT_ENV)
        return None
    logger.info("poller: each settled step reports to $%s", HEARTBEAT_ENV)
    return partial(send_heartbeat, url)


def fetch_feed(url: str) -> bytes:
    deadline = time.monotonic() + FETCH_DEADLINE_SEC
    with safe_urlopen(url, timeout=FETCH_TIMEOUT_SEC, max_bytes=MAX_POLL_BYTES, deadline=deadline) as resp:
        return resp.read()


def rt_dir(archive_root: Path, agency_id: int) -> Path:
    return archive_root / str(agency_id) / "rt"


def write_poll(agency_rt_dir: Path, at: datetime, raw: bytes) -> str:
    """Archive one poll atomically and return its member name."""
    member = member_name(at)
    path = agency_rt_dir / member
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(raw)
    os.replace(part, path)
    return member


def pack_closed_days(agency_rt_dir: Path, utc_today: date) -> list[Path]:
    """Pack every day directory before *utc_today* into ``<day>.tar.gz``, as
    `rotate-day.sh` does: members ``<day>/TripUpdate_HHMMSS.pb``, written to a
    ``.part`` and renamed, an existing tarball kept, the directory removed.
    Returns the tarballs written."""
    written: list[Path] = []
    if not agency_rt_dir.is_dir():
        return written
    for day_dir in sorted(agency_rt_dir.iterdir()):
        if not (day_dir.is_dir() and _DAY_DIR.fullmatch(day_dir.name)):
            continue
        if day_dir.name >= f"{utc_today:%Y%m%d}":
            continue
        tarball = agency_rt_dir / f"{day_dir.name}.tar.gz"
        if not tarball.exists():
            part = tarball.with_name(tarball.name + ".part")
            with tarfile.open(part, "w:gz") as tf:
                tf.add(day_dir, arcname=day_dir.name, filter=_skip_partial)
            os.replace(part, tarball)
            written.append(tarball)
        shutil.rmtree(day_dir)
        logger.info("poller: rotated %s", tarball)
    return written


def _skip_partial(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if info.name.endswith(".part") else info


def _tarball_day(tarball: Path) -> str:
    return tarball.name.removesuffix(".tar.gz")


def ingested_through(agency_rt_dir: Path) -> str | None:
    marker = agency_rt_dir / INGESTED_MARKER
    try:
        day = marker.read_text().strip()
    except FileNotFoundError:
        return None
    return day if _DAY_DIR.fullmatch(day) else None


def mark_ingested_through(agency_rt_dir: Path, day: str) -> None:
    marker = agency_rt_dir / INGESTED_MARKER
    part = marker.with_name(marker.name + ".part")
    part.write_text(f"{day}\n")
    os.replace(part, marker)


def _record_hold(agency_rt_dir: Path, day: str, utc_today: date) -> int:
    """Count one more UTC day holding the marker at *day* (a rerun on the
    same UTC day counts once) and return the count."""
    held = agency_rt_dir / HELD_MARKER
    try:
        prev_day, prev_count, counted_on = held.read_text().split()
        count = int(prev_count)
    except (FileNotFoundError, ValueError):
        prev_day, count, counted_on = "", 0, ""
    today = f"{utc_today:%Y%m%d}"
    if prev_day != day:
        count, counted_on = 0, ""
    if counted_on != today:
        count += 1
    part = held.with_name(held.name + ".part")
    part.write_text(f"{day} {count} {today}\n")
    os.replace(part, held)
    return count


def ingest_window(agency_rt_dir: Path) -> list[Path]:
    """The tarballs the next ingest reads: every one after the marker, plus
    the newest at or before it, whose UTC day holds the first hours of the
    JST day after it. All of them without a marker; none when nothing is new."""
    tarballs = sorted(
        (p for p in agency_rt_dir.glob("*.tar.gz") if _DAY_DIR.fullmatch(_tarball_day(p))), key=_tarball_day
    )
    marker = ingested_through(agency_rt_dir)
    if marker is None:
        return tarballs
    after = [p for p in tarballs if _tarball_day(p) > marker]
    if not after:
        return []
    return [p for p in tarballs if _tarball_day(p) <= marker][-1:] + after


def archived_agency_ids(archive_root: Path) -> list[int]:
    if not archive_root.is_dir():
        return []
    return sorted(int(p.name) for p in archive_root.iterdir() if p.name.isdigit() and (p / "rt").is_dir())


def _stage(conn, stage: str, agency_id: int, work: Callable[[], Any]) -> str:
    """Run one stage inside its run row, as the cron sweep records its own.

    Returns OK, FAILED, or SKIPPED for an agency with no timetable loaded,
    which no retry heals: only an operator loading one does."""
    from pipeline import runs as pipeline_runs
    from pipeline.ingest import StaticTimetableMissing

    try:
        with pipeline_runs.record_run(conn, stage, agency_id=agency_id) as run:
            result = work()
            if stage == "analyze":
                run.rebuild_headline = result
            else:
                run.rows = result
    except StaticTimetableMissing as exc:
        logger.warning("poller: %s skipped: %s", stage, exc)
        return SKIPPED
    except Exception:
        logger.exception("poller: %s failed for agency %s", stage, agency_id)
        return FAILED
    return OK


def _ingest_stage(conn, ch_client, archive_root: Path, agency_id: int, *, utc_today: date | None = None) -> str:
    """Ingest the agency's window and move its marker as far as the run
    allows. Any OSError here is this agency's failed ingest, not the step's."""
    utc_today = utc_today or datetime.now(timezone.utc).date()
    from pipeline.ingest import IngestReport, ingest

    agency_rt = rt_dir(archive_root, agency_id)
    try:
        marker = ingested_through(agency_rt)
        tarballs = ingest_window(agency_rt)
        if not tarballs:
            logger.info("poller: agency %s has no tarball after %s to ingest", agency_id, marker)
            return OK
        logger.info(
            "poller: agency %s ingests %d tarball(s) %s..%s (ingested through %s)",
            agency_id,
            len(tarballs),
            _tarball_day(tarballs[0]),
            _tarball_day(tarballs[-1]),
            marker,
        )
        report = IngestReport()
        with tempfile.TemporaryDirectory(prefix=f"poller-{agency_id}-") as staging:
            for tarball in tarballs:
                os.symlink(tarball.resolve(), Path(staging) / tarball.name)
            work = partial(ingest, staging, agency_id, conn, ch_client, report=report)
            outcome = _stage(conn, "ingest", agency_id, work)
        if outcome != OK:
            return outcome
        if report.transient_unplaced:
            logger.warning("poller: agency %s ingest left files a rerun may store; its marker stays", agency_id)
            return FAILED
        if report.transient_days:
            first = min(report.transient_days)
            hold = f"{first - timedelta(days=1):%Y%m%d}"
            held = _record_hold(agency_rt, hold, utc_today)
            if held >= MAX_HOLDS:
                logger.error(
                    "poller: agency %s ingest has failed on day %s in %d UTC days' steps (%s); its marker moves "
                    "past it, and `gtfs_pipeline.py ingest %s --agency-id %s` reads it again by hand",
                    agency_id,
                    f"{first:%Y%m%d}",
                    held,
                    report.errors.get(first, "no error recorded"),
                    agency_rt,
                    agency_id,
                )
                mark_ingested_through(agency_rt, _tarball_day(tarballs[-1]))
                (agency_rt / HELD_MARKER).unlink(missing_ok=True)
                return OK
            mark_ingested_through(agency_rt, hold)
            logger.warning(
                "poller: agency %s ingest failed on day(s) %s for a reason a rerun may clear; ingested through %s",
                agency_id,
                sorted(f"{d:%Y%m%d}" for d in report.transient_days),
                hold,
            )
            return FAILED
        mark_ingested_through(agency_rt, _tarball_day(tarballs[-1]))
        (agency_rt / HELD_MARKER).unlink(missing_ok=True)
        return OK
    except OSError:
        logger.exception("poller: agency %s ingest could not stage its archive", agency_id)
        return FAILED


def run_daily_step(archive_root: Path, plan: Plan) -> Plan:
    """Run each agency's planned stages (promote, ingest, analyze, in that
    order) under the ingest/analyze lock the cron sweep takes, then check the
    analyzed agencies' aggregates are fresh. Returns the stages that failed;
    every planned one when the lock is busy."""
    from pipeline import runs as pipeline_runs
    from pipeline.analyze import analyze
    from pipeline.clickhouse import ANALYZE_SEND_RECEIVE_TIMEOUT_SEC
    from pipeline.freshness import check_agg_freshness
    from pipeline.locks import try_lock_ingest_analyze_timed
    from pipeline.promote import promote_closed_days

    everything = {aid: set(stages) & set(_STAGE_ORDER) for aid, stages in plan.items()}
    if not any(everything.values()):
        return {}
    with open_stores(send_receive_timeout=ANALYZE_SEND_RECEIVE_TIMEOUT_SEC) as stores:
        conn = stores.conn
        got_lock, lock_wait_ms = try_lock_ingest_analyze_timed(conn)
        conn.autocommit = False
        if not got_lock:
            logger.warning("poller: another ingest/analyze run holds the lock; the daily step waits")
            pipeline_runs.start_run(conn, "ingest", status="skipped", lock_wait_ms=lock_wait_ms)
            return everything
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agency_id FROM agencies WHERE deleted_at IS NULL AND agency_id = ANY(%s) ORDER BY agency_id",
                (sorted(everything),),
            )
            agency_ids = [row[0] for row in cur.fetchall()]
        ch_client = stores.ch
        db_url = stores.db_url
        failed: Plan = {}
        analyzed = []
        for aid in agency_ids:
            for stage in _STAGE_ORDER:
                if stage not in everything[aid]:
                    continue
                if stage == "promote":
                    outcome = _stage(conn, stage, aid, partial(promote_closed_days, aid, conn, ch_client))
                elif stage == "ingest":
                    outcome = _ingest_stage(conn, ch_client, archive_root, aid)
                else:
                    outcome = _stage(conn, stage, aid, partial(analyze, aid, conn, ch_client, db_url=db_url))
                    analyzed.append(aid)
                if outcome == FAILED:
                    failed.setdefault(aid, set()).add(stage)
        if analyzed:
            try:
                stale = check_agg_freshness(conn, ch_client, analyzed)
                if stale:
                    logger.error("poller: aggregates still stale after analyze: %s", [s.agency_id for s in stale])
            except Exception:
                logger.exception("poller: the aggregate freshness check failed")
        return failed


def run_reprobe() -> None:
    """Renew the RT field-coverage verdicts that are due, as the cron poke does."""
    from pipeline.rt_coverage_reprobe import reprobe_due_agencies

    with open_stores() as stores:
        reprobe_due_agencies(stores.conn, stores.db_url)


def open_poll_stores() -> AbstractContextManager[Stores]:
    return open_stores(send_receive_timeout=POLL_CH_TIMEOUT_SEC)


def _plan(keys: Iterable[schedule.Key]) -> Plan:
    plan: Plan = {}
    for _, aid, stage in keys:
        plan.setdefault(aid, set()).add(stage)
    return plan


class Poller:
    """Polls every enabled agency each *interval_sec* until *stop* is set.

    The fetcher, clock, stores and background jobs are injectable so a test
    can drive the loop across day boundaries without a network or a wall
    clock.
    """

    def __init__(
        self,
        archive_root: Path,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        fetch: Fetcher = fetch_feed,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        stores: StoresFactory = open_poll_stores,
        daily_step: DailyStep = run_daily_step,
        reprobe: Callable[[], None] = run_reprobe,
        heartbeat: Heartbeat | None = None,
        fetch_workers: int = FETCH_WORKERS,
        fetch_deadline_sec: float = FETCH_DEADLINE_SEC,
        stop: threading.Event | None = None,
    ):
        if interval_sec <= 0:
            raise ValueError(f"poll interval must be positive, got {interval_sec}")
        self.archive_root = archive_root
        self.interval_sec = interval_sec
        self._fetch = fetch
        self._clock = clock
        self._stores = stores
        self._daily_step = daily_step
        self._reprobe = reprobe
        self._heartbeat = heartbeat
        self._fetch_workers = fetch_workers
        self._fetch_deadline_sec = fetch_deadline_sec
        self._pool: ThreadPoolExecutor | None = None
        self.stop = stop or threading.Event()
        self.fetch_failures: Counter[int] = Counter()
        self.push_failures: Counter[int] = Counter()
        self.archive_failures: Counter[int] = Counter()
        self.pushed: Counter[int] = Counter()
        self._roster: list[tuple[int, str]] = []
        self._lock = threading.Lock()
        self._schedule = schedule.State()
        self._daily_thread: threading.Thread | None = None
        self._reprobe_at: datetime | None = None
        self._reprobe_thread: threading.Thread | None = None

    @property
    def schedule_state(self) -> schedule.State:
        with self._lock:
            return self._schedule

    def run(self) -> None:
        self._remove_partial_polls()
        logger.info("poller: polling every %ss into %s", self.interval_sec, self.archive_root)
        try:
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    self.poll_cycle()
                    self.maybe_start_daily()
                    self.maybe_start_reprobe()
                except Exception:
                    logger.exception("poller: cycle failed")
                self.stop.wait(max(0.0, self.interval_sec - (time.monotonic() - started)))
        finally:
            self.close()
        if self._daily_thread is not None and self._daily_thread.is_alive():
            logger.warning("poller: stopping while the daily step runs; it runs again on the next start")
        logger.info("poller: stopped")

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # --- polling --------------------------------------------------------------

    def poll_cycle(self) -> None:
        with ExitStack() as stack:
            stores: Stores | None = None
            try:
                stores = stack.enter_context(self._stores())
                with stores.conn.cursor() as cur:
                    cur.execute(_ENABLED_AGENCIES_SQL)
                    self._roster = [(row[0], row[1]) for row in cur.fetchall()]
            except Exception as exc:
                logger.error(
                    "poller: Postgres unreachable, polling the last known %d agencies: %s: %s",
                    len(self._roster),
                    type(exc).__name__,
                    redact_urls_in_text(str(exc)),
                )
                stores = None
            if not self._roster or self.stop.is_set():
                return
            self._collect(list(self._roster), stores)

    def _fetch_one(self, feed_url: str) -> tuple[datetime, bytes | Exception, datetime]:
        at = self._clock()
        try:
            raw: bytes | Exception = self._fetch(feed_url)
        except Exception as exc:
            raw = exc
        return at, raw, self._clock()

    def _collect(self, roster: list[tuple[int, str]], stores: Stores | None) -> None:
        """Fetch every feed on the pool, and archive then push each poll as it
        arrives. When the loop ends (the cycle's deadline, or a stop), a fetch
        already back is archived, unpushed; one still running is dropped and
        never written, so once this returns no later write lands in a day
        directory the daily step may be packing."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._fetch_workers, thread_name_prefix="poller-fetch")
        futures: dict[Future, tuple[int, str]] = {
            self._pool.submit(self._fetch_one, url): (aid, url) for aid, url in roster
        }
        waves = math.ceil(len(roster) / self._fetch_workers)
        end = time.monotonic() + waves * self._fetch_deadline_sec + 5
        pushing = stores is not None
        remaining = set(futures)
        while remaining and not self.stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            done, remaining = wait(remaining, timeout=min(left, 1.0), return_when=FIRST_COMPLETED)
            for fut in done:
                aid, url = futures[fut]
                pushing = self._take(aid, url, fut.result(), stores if pushing else None) and pushing
        for fut in remaining:
            aid, url = futures[fut]
            if fut.done() and not fut.cancelled():
                self._take(aid, url, fut.result(), None)
                continue
            fut.cancel()
            self.fetch_failures[aid] += 1
            logger.warning("poller: agency %s fetch of %s dropped, past its deadline", aid, redact_url(url))
        if stores is None:
            logger.warning("poller: Postgres unreachable; this cycle's polls are archived only")

    def _take(
        self, agency_id: int, feed_url: str, result: tuple[datetime, bytes | Exception, datetime], stores: Stores | None
    ) -> bool:
        """Archive one fetched poll, then push it when *stores* is given.
        Returns False when the push failed for a reason other than the poll
        itself, so the cycle stops pushing."""
        from pipeline.ingest import PayloadDecodeError, StaticTimetableMissing

        at, raw, fetched_at = result
        if isinstance(raw, Exception):
            self.fetch_failures[agency_id] += 1
            logger.warning(
                "poller: agency %s fetch of %s failed (%d so far): %s: %s",
                agency_id,
                redact_url(feed_url),
                self.fetch_failures[agency_id],
                type(raw).__name__,
                redact_urls_in_text(str(raw)),
            )
            return True
        try:
            member = write_poll(rt_dir(self.archive_root, agency_id), at, raw)
        except OSError:
            self.archive_failures[agency_id] += 1
            logger.exception("poller: agency %s poll could not be archived; it is not pushed", agency_id)
            return True
        if stores is None:
            self.push_failures[agency_id] += 1
            return True
        captured_at = fetched_at.astimezone(timezone.utc).isoformat()
        try:
            ingest_pushed_poll(agency_id, raw, captured_at, member, stores)
            self.pushed[agency_id] += 1
        except (UnknownAgency, PayloadDecodeError, StaticTimetableMissing) as exc:
            self.push_failures[agency_id] += 1
            logger.warning("poller: agency %s poll %s not stored: %s", agency_id, member, exc)
        except Exception as exc:
            self.push_failures[agency_id] += 1
            logger.error(
                "poller: agency %s poll %s not stored, and this cycle's later polls are archived only: %s: %s",
                agency_id,
                member,
                type(exc).__name__,
                redact_urls_in_text(str(exc)),
            )
            return False
        return True

    # --- background jobs ------------------------------------------------------

    def maybe_start_daily(self) -> str | None:
        """Start the step `pipeline.poller_schedule.decide` picks, once the
        last one has finished. Returns its kind."""
        if self._daily_thread is not None and self._daily_thread.is_alive():
            return None
        now = self._clock()
        archived = archived_agency_ids(self.archive_root)
        with self._lock:
            kind, keys, self._schedule = schedule.decide(now, archived, self._schedule)
        if not keys:
            return None
        logger.info("poller: starting the %s step: %s", kind, _plan(keys))
        self._daily_thread = threading.Thread(
            target=self._run_daily, args=(now.astimezone(timezone.utc).date(), keys), name="poller-daily", daemon=True
        )
        self._daily_thread.start()
        return kind

    def _run_daily(self, utc_today: date, keys: frozenset[schedule.Key]) -> None:
        plan = _plan(keys)
        failed: set[tuple[int, str]] = set()
        for aid, stages in plan.items():
            if "pack" not in stages:
                continue
            try:
                pack_closed_days(rt_dir(self.archive_root, aid), utc_today)
            except Exception:
                logger.exception("poller: packing agency %s failed; its step still runs", aid)
                failed.add((aid, "pack"))
        work = {aid: stages - {"pack"} for aid, stages in plan.items()}
        try:
            step_failed = self._daily_step(self.archive_root, work)
        except Exception:
            logger.exception("poller: daily step failed")
            step_failed = work
        failed |= {(aid, stage) for aid, stages in step_failed.items() for stage in stages}
        with self._lock:
            before = self._schedule
            self._schedule = after = schedule.settle(self._clock(), before, keys, failed)
        newly = schedule.newly_exhausted(before, after)
        if failed:
            logger.warning("poller: failed stages %s are retried in %s", sorted(failed), schedule.RETRY_AFTER)
        if newly:
            logger.error("poller: %s used every retry; they wait for their next day", newly)
        outcome = schedule.heartbeat(before, after)
        if outcome is not None and self._heartbeat is not None:
            self._heartbeat(outcome, f"used every retry: {newly}" if newly else "")

    def maybe_start_reprobe(self) -> bool:
        if self._reprobe_thread is not None and self._reprobe_thread.is_alive():
            return False
        now = self._clock()
        if self._reprobe_at is not None and now < self._reprobe_at:
            return False
        self._reprobe_at = now + timedelta(seconds=REPROBE_INTERVAL_SEC)
        self._reprobe_thread = threading.Thread(target=self._run_reprobe, name="poller-reprobe", daemon=True)
        self._reprobe_thread.start()
        return True

    def _run_reprobe(self) -> None:
        try:
            self._reprobe()
        except Exception:
            logger.exception("poller: RT field-coverage re-probe failed")

    def join_background(self, timeout: float | None = None) -> None:
        for thread in (self._daily_thread, self._reprobe_thread):
            if thread is not None:
                thread.join(timeout)

    def _remove_partial_polls(self) -> None:
        for part in self.archive_root.glob("*/rt/*/TripUpdate_*.part"):
            part.unlink(missing_ok=True)


def install_stop_handlers(stop: threading.Event) -> None:
    """SIGTERM and SIGINT end the loop once the cycle in flight has archived
    what arrived."""

    def _handler(signum, frame) -> None:
        logger.info("poller: signal %s received; stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
