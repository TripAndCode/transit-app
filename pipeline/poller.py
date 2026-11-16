"""In-repo GTFS-RT collector for a standalone deployment (`gtfs_pipeline.py poll`).

It produces what the Oracle collector (`oracle_cloud/v3/bin/rt-poller.sh`,
`rotate-day.sh`) produces and hands it to the same code the app already runs.

Every cycle, for every enabled agency:
- the feed is fetched (a few at a time, so one dead feed delays no other) and
  archived as ``<archive>/<agency_id>/rt/<UTC YYYYMMDD>/TripUpdate_HHMMSS.pb``,
  whether or not the stores are reachable;
- then the poll is pushed into `updates_live` through
  `pipeline.collector_push.ingest_pushed_poll`, the function the collector's
  HTTP push uses, so it carries the same provenance. The file is in place
  before its push, which keeps every pushed poll in the archive: archive
  replacement counts a pushed poll without its member as a gap.

On a worker thread, so polls keep their pace:
- the first cycle of each UTC day packs every closed day directory into
  ``<YYYYMMDD>.tar.gz`` in `rotate-day.sh`'s format, then, under the lock the
  cron sweep takes, promotes the closed JST days, ingests the tarballs not yet
  ingested with `pipeline.ingest.ingest` (where the day promotion stored is
  replaced once both of its tarballs are read), analyzes, and checks that the
  aggregates are fresh;
- the first cycle of each JST day promotes and analyzes, so yesterday reaches
  history at JST midnight rather than at the UTC rollover nine hours later;
- a stage that failed is retried on its own a bounded number of times a day;
- once an hour, the RT field-coverage verdicts that are due are re-probed
  (`pipeline.rt_coverage_reprobe`, which waits for each agency's service
  hours).

Ingest reads every tarball after the agency's ``.ingested-through`` marker,
plus the one before it, since a JST day spans two UTC days. The marker moves
only when an ingest succeeds, so days that waited out an outage or a missing
timetable are read by the next run that can. ``gtfs_pipeline.py ingest
<archive>/<agency_id>/rt --agency-id <id>`` reads all of them by hand.

Feed URLs come only from the agencies table, and are fetched through
`pipeline.url_guard.safe_urlopen` (http(s) only, every redirect hop vetted,
body capped). A failed fetch, archive write or push is logged and counted, and
the next poll goes ahead. The tarballs stay in the archive volume; nothing
uploads or prunes them.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import tarfile
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.collector_push import MAX_POLL_BYTES, UnknownAgency, ingest_pushed_poll, member_name
from pipeline.stores import Stores, open_stores
from pipeline.url_guard import redact_url, redact_urls_in_text, safe_urlopen

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 60.0
FETCH_TIMEOUT_SEC = 15
FETCH_WORKERS = 4
DAILY_RETRY_SEC = 15 * 60
MAX_RETRIES_PER_DAY = 8
REPROBE_INTERVAL_SEC = 60 * 60
INGESTED_MARKER = ".ingested-through"

UTC_DAY_STAGES = frozenset({"pack", "promote", "ingest", "analyze"})
JST_DAY_STAGES = frozenset({"promote", "analyze"})
_STAGE_ORDER = ("promote", "ingest", "analyze")

_JST = ZoneInfo("Asia/Tokyo")
_DAY_DIR = re.compile(r"[0-9]{8}")
_ENABLED_AGENCIES_SQL = (
    "SELECT agency_id, feed_url FROM agencies "
    "WHERE deleted_at IS NULL AND feed_url IS NOT NULL AND feed_url <> '' ORDER BY agency_id"
)

# Each agency's stages to run, or, returned, the stages that failed.
Plan = dict[int, set[str]]
Fetcher = Callable[[str], bytes]
StoresFactory = Callable[[], AbstractContextManager[Stores]]
DailyStep = Callable[[Path, Plan], Plan]

OK, SKIPPED, FAILED = "ok", "skipped", "failed"


def fetch_feed(url: str) -> bytes:
    with safe_urlopen(url, timeout=FETCH_TIMEOUT_SEC, max_bytes=MAX_POLL_BYTES) as resp:
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


def _ingest_stage(conn, ch_client, archive_root: Path, agency_id: int) -> str:
    from pipeline.ingest import ingest

    agency_rt = rt_dir(archive_root, agency_id)
    tarballs = ingest_window(agency_rt)
    if not tarballs:
        logger.info("poller: agency %s has no tarball after %s to ingest", agency_id, ingested_through(agency_rt))
        return OK
    first, last = _tarball_day(tarballs[0]), _tarball_day(tarballs[-1])
    logger.info(
        "poller: agency %s ingests %d tarball(s) %s..%s (ingested through %s)",
        agency_id,
        len(tarballs),
        first,
        last,
        ingested_through(agency_rt),
    )
    with tempfile.TemporaryDirectory(prefix=f"poller-{agency_id}-") as staging:
        for tarball in tarballs:
            os.symlink(tarball.resolve(), Path(staging) / tarball.name)
        outcome = _stage(conn, "ingest", agency_id, partial(ingest, staging, agency_id, conn, ch_client))
    if outcome == OK:
        mark_ingested_through(agency_rt, last)
    return outcome


def run_daily_step(archive_root: Path, plan: Plan) -> Plan:
    """Run each agency's planned stages (promote, ingest, analyze, in that
    order) under the ingest/analyze lock the cron sweep takes, then check the
    analyzed agencies' aggregates are fresh. Returns the stages that failed;
    every planned one when the lock is busy or a store is down."""
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


def _with_followups(plan: Plan) -> Plan:
    """A retried stage brings the stages that consume its result."""
    out: Plan = {}
    for aid, stages in plan.items():
        s = set(stages)
        if "pack" in s:
            s.add("ingest")
        if s & {"pack", "promote", "ingest"}:
            s.add("analyze")
        out[aid] = s
    return out


def _merge(into: Plan, more: Plan) -> None:
    for aid, stages in more.items():
        if stages:
            into.setdefault(aid, set()).update(stages)


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
        stores: StoresFactory = open_stores,
        daily_step: DailyStep = run_daily_step,
        reprobe: Callable[[], None] = run_reprobe,
        fetch_workers: int = FETCH_WORKERS,
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
        self._fetch_workers = fetch_workers
        self.stop = stop or threading.Event()
        self.fetch_failures: Counter[int] = Counter()
        self.push_failures: Counter[int] = Counter()
        self.archive_failures: Counter[int] = Counter()
        self.pushed: Counter[int] = Counter()
        self._roster: list[tuple[int, str]] = []
        self._lock = threading.Lock()
        self._utc_done_for: date | None = None
        self._jst_done_for: date | None = None
        self._retry: Plan = {}
        self._retry_at: datetime | None = None
        self._retries = 0
        self._daily_thread: threading.Thread | None = None
        self._reprobe_at: datetime | None = None
        self._reprobe_thread: threading.Thread | None = None

    @property
    def pending_retry(self) -> Plan:
        with self._lock:
            return {aid: set(stages) for aid, stages in self._retry.items()}

    def run(self) -> None:
        self._remove_partial_polls()
        logger.info("poller: polling every %ss into %s", self.interval_sec, self.archive_root)
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.poll_cycle()
                self.maybe_start_daily()
                self.maybe_start_reprobe()
            except Exception:
                logger.exception("poller: cycle failed")
            self.stop.wait(max(0.0, self.interval_sec - (time.monotonic() - started)))
        if self._daily_thread is not None and self._daily_thread.is_alive():
            logger.warning("poller: stopping while the daily step runs; it runs again on the next start")
        logger.info("poller: stopped")

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
            polls = self._fetch_and_archive(self._roster)
            self._push(polls, stores)

    def _fetch_one(self, feed_url: str) -> tuple[datetime, bytes | Exception, datetime]:
        at = self._clock()
        try:
            raw: bytes | Exception = self._fetch(feed_url)
        except Exception as exc:
            raw = exc
        return at, raw, self._clock()

    def _fetch_and_archive(self, roster: list[tuple[int, str]]) -> list[tuple[int, str, bytes, str]]:
        """Fetch every feed concurrently, then archive the polls one by one.
        Every write lands before this returns, so no later cycle writes into a
        day directory the daily step may be packing."""
        with ThreadPoolExecutor(max_workers=self._fetch_workers, thread_name_prefix="poller-fetch") as pool:
            results = list(pool.map(self._fetch_one, [url for _, url in roster]))
        polls = []
        for (agency_id, feed_url), (at, raw, fetched_at) in zip(roster, results, strict=True):
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
                continue
            try:
                member = write_poll(rt_dir(self.archive_root, agency_id), at, raw)
            except OSError:
                self.archive_failures[agency_id] += 1
                logger.exception("poller: agency %s poll could not be archived; it is not pushed", agency_id)
                continue
            polls.append((agency_id, member, raw, fetched_at.astimezone(timezone.utc).isoformat()))
        return polls

    def _push(self, polls: list[tuple[int, str, bytes, str]], stores: Stores | None) -> None:
        from pipeline.ingest import PayloadDecodeError, StaticTimetableMissing

        if not polls:
            return
        ch_client = None
        if stores is not None:
            try:
                ch_client = stores.ch
            except Exception as exc:
                logger.error(
                    "poller: ClickHouse unreachable: %s: %s", type(exc).__name__, redact_urls_in_text(str(exc))
                )
        if stores is None or ch_client is None:
            for agency_id, *_ in polls:
                self.push_failures[agency_id] += 1
            logger.warning("poller: %d poll(s) archived but not pushed", len(polls))
            return
        for agency_id, member, raw, captured_at in polls:
            try:
                ingest_pushed_poll(agency_id, raw, captured_at, member, stores.conn, ch_client)
                self.pushed[agency_id] += 1
            except (UnknownAgency, PayloadDecodeError, StaticTimetableMissing) as exc:
                self.push_failures[agency_id] += 1
                logger.warning("poller: agency %s poll %s not stored: %s", agency_id, member, exc)
            except Exception:
                self.push_failures[agency_id] += 1
                logger.exception("poller: agency %s poll %s not stored", agency_id, member)

    # --- background jobs ------------------------------------------------------

    def maybe_start_daily(self) -> str | None:
        """Start the step due, if the last one has finished: the UTC-day step
        on the first cycle of a UTC day (and of a start), the JST-day step on
        the first of a JST day, else a retry of what failed once its wait is
        over. Returns which started."""
        if self._daily_thread is not None and self._daily_thread.is_alive():
            return None
        now = self._clock()
        utc_today = now.astimezone(timezone.utc).date()
        jst_today = now.astimezone(_JST).date()
        archived = archived_agency_ids(self.archive_root)
        with self._lock:
            if self._utc_done_for != utc_today:
                kind, plan = "utc", {aid: set(UTC_DAY_STAGES) for aid in archived}
                self._utc_done_for, self._jst_done_for = utc_today, jst_today
                self._retry, self._retries = {}, 0
            elif self._jst_done_for != jst_today:
                kind, plan = "jst", {aid: set(JST_DAY_STAGES) for aid in archived}
                self._jst_done_for = jst_today
            elif self._retry and self._retry_at is not None and now >= self._retry_at:
                if self._retries >= MAX_RETRIES_PER_DAY:
                    logger.error("poller: giving up on %s until the next UTC day", self._retry)
                    self._retry = {}
                    return None
                kind, plan = "retry", _with_followups(self._retry)
                self._retry, self._retries = {}, self._retries + 1
            else:
                return None
        if not plan:
            return None
        logger.info("poller: starting the %s step for %d agency(ies)", kind, len(plan))
        self._daily_thread = threading.Thread(
            target=self._run_daily, args=(utc_today, plan), name="poller-daily", daemon=True
        )
        self._daily_thread.start()
        return kind

    def _run_daily(self, utc_today: date, plan: Plan) -> None:
        failed: Plan = {}
        for aid, stages in plan.items():
            if "pack" not in stages:
                continue
            try:
                pack_closed_days(rt_dir(self.archive_root, aid), utc_today)
            except Exception:
                logger.exception("poller: packing agency %s failed; its step still runs", aid)
                failed[aid] = {"pack"}
        work = {aid: stages - {"pack"} for aid, stages in plan.items()}
        try:
            _merge(failed, self._daily_step(self.archive_root, work))
        except Exception:
            logger.exception("poller: daily step failed")
            _merge(failed, work)
        with self._lock:
            _merge(self._retry, failed)
            if self._retry:
                self._retry_at = self._clock() + timedelta(seconds=DAILY_RETRY_SEC)
                logger.warning("poller: retrying %s in %ss", self._retry, DAILY_RETRY_SEC)

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
    """SIGTERM and SIGINT end the loop after the poll in flight."""

    def _handler(signum, frame) -> None:
        logger.info("poller: signal %s received; stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
