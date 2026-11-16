"""In-repo GTFS-RT collector for a standalone deployment (`gtfs_pipeline.py poll`).

It produces what the Oracle collector (`oracle_cloud/v3/bin/rt-poller.sh`,
`rotate-day.sh`) produces and hands it to the same code the app already runs:

- Every poll of every enabled agency is archived as
  ``<archive>/<agency_id>/rt/<UTC YYYYMMDD>/TripUpdate_HHMMSS.pb``, then pushed
  into `updates_live` through `pipeline.collector_push.ingest_pushed_poll`, the
  function the collector's HTTP push uses, so it carries the same provenance.
  The file is in place before its push, which keeps every pushed poll in the
  archive: archive replacement counts a pushed poll without its member as a gap.
- Once a UTC day has closed, its directory is packed into ``<YYYYMMDD>.tar.gz``
  in `rotate-day.sh`'s format. Packing runs on the polling thread, between
  polls, so no poll can still be writing into a day being packed.
- Then, off the polling thread so polls keep their pace, the daily step runs
  what the cron sweep runs per agency under the same lock: promotion of closed
  JST days, archive ingest (`pipeline.ingest.ingest`) of the newest tarballs,
  and analyze. A JST day spans two UTC days, so ingest reads a folder holding
  both tarballs; that is when it can replace the day promotion stored.

Feed URLs come only from the agencies table, and are fetched through
`pipeline.url_guard.safe_urlopen` (http(s) only, every redirect hop vetted,
body capped). A failed fetch, archive write or push is logged and counted, and
the next poll goes ahead. Upload of the tarballs to object storage is not done
here; `Poller(on_packed=...)` is where it would attach.
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

from pipeline.collector_push import UnknownAgency, ingest_pushed_poll, member_name
from pipeline.url_guard import redact_url, redact_urls_in_text, safe_urlopen

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 60.0
# The collector push endpoint's own payload cap.
MAX_POLL_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT_SEC = 15
# A failed or displaced daily step is tried again after this long.
DAILY_RETRY_SEC = 15 * 60
# The UTC days of tarballs the daily step hands to ingest, ending yesterday.
# Two make a JST day whole; the third lets a step that failed for a day still
# reach the day before it. Ingest skips every member it already holds.
INGEST_WINDOW_DAYS = 3

_DAY_DIR = re.compile(r"[0-9]{8}")
_ENABLED_AGENCIES_SQL = (
    "SELECT agency_id, feed_url FROM agencies "
    "WHERE deleted_at IS NULL AND feed_url IS NOT NULL AND feed_url <> '' ORDER BY agency_id"
)

Fetcher = Callable[[str], bytes]
Stores = Callable[[], Any]
DailyStep = Callable[[Path, date], bool]


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


def window_tarballs(agency_rt_dir: Path, utc_today: date) -> list[Path]:
    days = {f"{utc_today - timedelta(days=n):%Y%m%d}" for n in range(1, INGEST_WINDOW_DAYS + 1)}
    return sorted(p for p in agency_rt_dir.glob("*.tar.gz") if p.name.removesuffix(".tar.gz") in days)


def archived_agency_ids(archive_root: Path) -> list[int]:
    if not archive_root.is_dir():
        return []
    return sorted(int(p.name) for p in archive_root.iterdir() if p.name.isdigit() and (p / "rt").is_dir())


@contextmanager
def open_stores() -> Iterator[tuple[Any, Any]]:
    """A Postgres connection and a ClickHouse client for one poll cycle, set
    up as the collector push endpoint sets up its own."""
    import psycopg2

    from pipeline.clickhouse import get_client
    from pipeline.db import DEFAULT_DATABASE_URL

    conn = psycopg2.connect(os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    ch_client = None
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        ch_client = get_client()
        yield conn, ch_client
    finally:
        try:
            conn.close()
        finally:
            if ch_client is not None:
                ch_client.close()


def run_daily_step(archive_root: Path, utc_today: date) -> bool:
    """Promote, ingest the newest tarballs, and analyze, for every enabled
    agency with an archive, under the ingest/analyze lock the cron sweep
    takes. Returns whether every stage of every agency succeeded; a busy
    lock counts as not."""
    import psycopg2

    from pipeline import runs as pipeline_runs
    from pipeline.analyze import analyze
    from pipeline.clickhouse import ANALYZE_SEND_RECEIVE_TIMEOUT_SEC, get_client
    from pipeline.db import DEFAULT_DATABASE_URL
    from pipeline.ingest import ingest
    from pipeline.locks import try_lock_ingest_analyze_timed
    from pipeline.promote import promote_closed_days

    archived = archived_agency_ids(archive_root)
    if not archived:
        return True
    db_url = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    conn = psycopg2.connect(db_url)
    ch_client = None
    ok = True
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        got_lock, lock_wait_ms = try_lock_ingest_analyze_timed(conn)
        conn.autocommit = False
        if not got_lock:
            logger.warning("poller: another ingest/analyze run holds the lock; the daily step waits")
            pipeline_runs.start_run(conn, "ingest", status="skipped", lock_wait_ms=lock_wait_ms)
            return False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agency_id FROM agencies WHERE deleted_at IS NULL AND agency_id = ANY(%s) ORDER BY agency_id",
                (archived,),
            )
            agency_ids = [row[0] for row in cur.fetchall()]
        ch_client = get_client(send_receive_timeout=ANALYZE_SEND_RECEIVE_TIMEOUT_SEC)
        for aid in agency_ids:
            with tempfile.TemporaryDirectory(prefix=f"poller-{aid}-") as staging:
                for tarball in window_tarballs(rt_dir(archive_root, aid), utc_today):
                    os.symlink(tarball.resolve(), Path(staging) / tarball.name)
                ok &= _stage(conn, "promote", aid, partial(promote_closed_days, aid, conn, ch_client))
                ok &= _stage(conn, "ingest", aid, partial(ingest, staging, aid, conn, ch_client))
                ok &= _stage(conn, "analyze", aid, partial(analyze, aid, conn, ch_client, db_url=db_url))
    finally:
        try:
            conn.close()
        finally:
            if ch_client is not None:
                ch_client.close()
    return ok


def _stage(conn, stage: str, agency_id: int, work: Callable[[], Any]) -> bool:
    """Run one stage of the daily step inside its run row, as the cron sweep
    records its own. Returns whether a retry is owed: an agency with no
    timetable loaded is not, since only an operator loading one heals it."""
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
    except Exception:
        logger.exception("poller: %s failed for agency %s", stage, agency_id)
        return False
    return True


class Poller:
    """Polls every enabled agency each *interval_sec* until *stop* is set.

    The fetcher, clock, stores and daily step are injectable so a test can
    drive the loop across day boundaries without a network or a wall clock.
    """

    def __init__(
        self,
        archive_root: Path,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        fetch: Fetcher = fetch_feed,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        stores: Stores = open_stores,
        daily_step: DailyStep = run_daily_step,
        on_packed: Callable[[int, Path], None] | None = None,
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
        self._on_packed = on_packed
        self.stop = stop or threading.Event()
        self.fetch_failures: Counter[int] = Counter()
        self.push_failures: Counter[int] = Counter()
        self.archive_failures: Counter[int] = Counter()
        self.pushed: Counter[int] = Counter()
        self._daily_done_for: date | None = None
        self._daily_retry_at: datetime | None = None
        self._daily_thread: threading.Thread | None = None

    def run(self) -> None:
        self._remove_partial_polls()
        logger.info("poller: polling every %ss into %s", self.interval_sec, self.archive_root)
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.poll_cycle()
                self.maybe_start_daily()
            except Exception:
                logger.exception("poller: cycle failed")
            self.stop.wait(max(0.0, self.interval_sec - (time.monotonic() - started)))
        if self._daily_thread is not None and self._daily_thread.is_alive():
            logger.warning("poller: stopping while the daily step runs; it runs again on the next start")
        logger.info("poller: stopped")

    def poll_cycle(self) -> None:
        try:
            with self._stores() as (conn, ch_client):
                with conn.cursor() as cur:
                    cur.execute(_ENABLED_AGENCIES_SQL)
                    agencies = cur.fetchall()
                for agency_id, feed_url in agencies:
                    if self.stop.is_set():
                        return
                    self._poll_agency(agency_id, feed_url, conn, ch_client)
        except Exception as exc:
            logger.error("poller: cycle ended early: %s: %s", type(exc).__name__, redact_urls_in_text(str(exc)))

    def _poll_agency(self, agency_id: int, feed_url: str, conn, ch_client) -> None:
        from pipeline.ingest import PayloadDecodeError, StaticTimetableMissing

        at = self._clock()
        try:
            raw = self._fetch(feed_url)
        except Exception as exc:
            self.fetch_failures[agency_id] += 1
            logger.warning(
                "poller: agency %s fetch of %s failed (%d so far): %s: %s",
                agency_id,
                redact_url(feed_url),
                self.fetch_failures[agency_id],
                type(exc).__name__,
                redact_urls_in_text(str(exc)),
            )
            return
        try:
            member = write_poll(rt_dir(self.archive_root, agency_id), at, raw)
        except OSError:
            self.archive_failures[agency_id] += 1
            logger.exception("poller: agency %s poll could not be archived; it is not pushed", agency_id)
            return
        captured_at = self._clock().astimezone(timezone.utc).isoformat()
        try:
            ingest_pushed_poll(agency_id, raw, captured_at, member, conn, ch_client)
            self.pushed[agency_id] += 1
        except (UnknownAgency, PayloadDecodeError, StaticTimetableMissing) as exc:
            self.push_failures[agency_id] += 1
            logger.warning("poller: agency %s poll %s not stored: %s", agency_id, member, exc)
        except Exception:
            self.push_failures[agency_id] += 1
            logger.exception("poller: agency %s poll %s not stored", agency_id, member)

    def maybe_start_daily(self) -> bool:
        """Pack each closed UTC day and start the daily step, once per UTC
        day, the first cycle after it begins (and the first cycle after a
        start, to catch up). Returns whether a step was started."""
        now = self._clock()
        today = now.astimezone(timezone.utc).date()
        if self._daily_done_for == today:
            return False
        if self._daily_retry_at is not None and now < self._daily_retry_at:
            return False
        if self._daily_thread is not None and self._daily_thread.is_alive():
            return False
        for agency_id in archived_agency_ids(self.archive_root):
            agency_rt = rt_dir(self.archive_root, agency_id)
            for tarball in pack_closed_days(agency_rt, today):
                if self._on_packed is not None:
                    self._on_packed(agency_id, tarball)
        self._daily_thread = threading.Thread(target=self._run_daily, args=(today,), name="poller-daily", daemon=True)
        self._daily_thread.start()
        return True

    def _run_daily(self, today: date) -> None:
        try:
            ok = self._daily_step(self.archive_root, today)
        except Exception:
            logger.exception("poller: daily step failed")
            ok = False
        if ok:
            self._daily_done_for = today
            self._daily_retry_at = None
        else:
            self._daily_retry_at = self._clock() + timedelta(seconds=DAILY_RETRY_SEC)
            logger.warning("poller: daily step for %s incomplete; it is tried again", today)

    @property
    def daily_done_for(self) -> date | None:
        """The UTC day whose daily step last completed in full."""
        return self._daily_done_for

    def join_daily(self, timeout: float | None = None) -> None:
        if self._daily_thread is not None:
            self._daily_thread.join(timeout)

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
