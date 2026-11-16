"""The standalone poller's naming, packing, scheduling and failure handling
(pipeline.poller), with no network and no database."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tarfile
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from pipeline import poller
from pipeline.collector_push import MAX_POLL_BYTES, PUSH_PREFIX, SOURCE_FILE_RE, member_name
from pipeline.url_guard import FeedURLError

ROOT = Path(__file__).resolve().parents[2]
ROTATE_DAY = ROOT / "oracle_cloud" / "v3" / "bin" / "rotate-day.sh"
_JST = ZoneInfo("Asia/Tokyo")
_ONE = ((1, "https://feed.example/1.pb"),)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Cursor:
    def __init__(self, agencies):
        self._agencies = agencies

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return list(self._agencies)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStores:
    def __init__(self, agencies, *, clickhouse_up=True):
        self.conn = MagicMock()
        self.conn.cursor.side_effect = lambda: _Cursor(agencies)
        self._clickhouse_up = clickhouse_up

    @property
    def ch(self):
        if not self._clickhouse_up:
            raise OSError("clickhouse: connection refused")
        return MagicMock()


def _stores(agencies, **kwargs):
    @contextmanager
    def stores():
        yield _FakeStores(agencies, **kwargs)

    return stores


def _postgres_down():
    @contextmanager
    def stores():
        raise OSError("could not connect to server")
        yield

    return stores


def _poller(tmp_path, *, agencies=_ONE, stores=None, **kwargs):
    kwargs.setdefault("clock", _Clock(_utc(2026, 4, 2, 10, 0, 0)))
    kwargs.setdefault("fetch", lambda url: b"poll")
    kwargs.setdefault("daily_step", lambda root, plan: {})
    kwargs.setdefault("reprobe", lambda: None)
    return poller.Poller(tmp_path, stores=stores or _stores(agencies), **kwargs)


# --- naming -----------------------------------------------------------------


def test_a_poll_is_named_for_its_utc_day_and_time():
    at = datetime(2026, 4, 3, 8, 59, 30, tzinfo=_JST)
    assert member_name(at) == "20260402/TripUpdate_235930.pb"
    assert SOURCE_FILE_RE.fullmatch(member_name(at))


def test_the_push_prefix_is_the_one_archive_replacement_reads():
    from pipeline.clickhouse import COLLECTOR_PUSHED

    assert f"'{PUSH_PREFIX}'" in COLLECTOR_PUSHED


def test_a_poll_is_archived_whole_under_its_member_name(tmp_path):
    member = poller.write_poll(tmp_path, _utc(2026, 4, 2, 0, 0, 5), b"bytes")
    assert member == "20260402/TripUpdate_000005.pb"
    assert (tmp_path / member).read_bytes() == b"bytes"
    assert not list(tmp_path.rglob("*.part"))


# --- packing ----------------------------------------------------------------


def _day_dir(rt: Path, day: str, times: list[str]) -> None:
    (rt / day).mkdir(parents=True)
    for hhmmss in times:
        (rt / day / f"TripUpdate_{hhmmss}.pb").write_bytes(hhmmss.encode())


def _pb_members(tarball: Path) -> dict[str, bytes]:
    with tarfile.open(tarball, "r:gz") as tf:
        out = {}
        for m in tf.getmembers():
            if m.isfile():
                fobj = tf.extractfile(m)
                assert fobj is not None
                out[m.name] = fobj.read()
        return out


def test_a_closed_day_is_packed_and_today_is_left_alone(tmp_path):
    _day_dir(tmp_path, "20260401", ["235959", "120000"])
    _day_dir(tmp_path, "20260402", ["000001"])
    (tmp_path / "20260401" / "TripUpdate_235960.pb.part").write_bytes(b"torn")

    written = poller.pack_closed_days(tmp_path, date(2026, 4, 2))

    assert written == [tmp_path / "20260401.tar.gz"]
    assert _pb_members(written[0]) == {
        "20260401/TripUpdate_120000.pb": b"120000",
        "20260401/TripUpdate_235959.pb": b"235959",
    }
    assert not (tmp_path / "20260401").exists()
    assert (tmp_path / "20260402" / "TripUpdate_000001.pb").exists()
    assert not list(tmp_path.glob("*.part"))


def test_an_existing_tarball_is_kept_and_its_directory_removed(tmp_path):
    _day_dir(tmp_path, "20260401", ["120000"])
    (tmp_path / "20260401.tar.gz").write_bytes(b"already packed")

    assert poller.pack_closed_days(tmp_path, date(2026, 4, 2)) == []
    assert (tmp_path / "20260401.tar.gz").read_bytes() == b"already packed"
    assert not (tmp_path / "20260401").exists()


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None, reason="needs bash and tar")
def test_packing_matches_the_collectors_rotate_day(tmp_path):
    yesterday = f"{datetime.now(timezone.utc).date() - timedelta(days=1):%Y%m%d}"
    ours = tmp_path / "ours"
    theirs = tmp_path / "collector" / "data" / "1" / "rt"
    for rt in (ours, theirs):
        _day_dir(rt, yesterday, ["000000", "123456", "235959"])

    poller.pack_closed_days(ours, datetime.now(timezone.utc).date())
    subprocess.run(
        ["bash", str(ROTATE_DAY)],
        # COPYFILE_DISABLE keeps macOS's bsdtar from adding AppleDouble members
        # the collector's GNU tar never writes.
        env={**os.environ, "COLLECTOR_BASE": str(tmp_path / "collector"), "COPYFILE_DISABLE": "1"},
        check=True,
        capture_output=True,
    )

    name = f"{yesterday}.tar.gz"
    assert _pb_members(ours / name) == _pb_members(theirs / name)


def test_a_tarball_written_this_way_is_one_ingest_reads(tmp_path):
    """The member names ingest reads from the tarball are the names pushes carry."""
    from pipeline.ingest import _date_dir

    rt = tmp_path / "rt"
    member = poller.write_poll(rt, _utc(2026, 4, 1, 12, 0, 0), b"x")
    (tarball,) = poller.pack_closed_days(rt, date(2026, 4, 2))
    with tarfile.open(tarball, "r:gz") as tf:
        names = {f"{_date_dir(Path(m.name).parent.name)}/{Path(m.name).name}" for m in tf.getmembers() if m.isfile()}
    assert names == {member}


# --- the ingest window ------------------------------------------------------


def _tarballs(rt: Path, *days: str) -> None:
    rt.mkdir(parents=True, exist_ok=True)
    for day in days:
        (rt / f"{day}.tar.gz").write_bytes(b"")


def _names(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


def test_with_no_marker_every_tarball_is_read(tmp_path):
    _tarballs(tmp_path, "20260330", "20260331", "20260401")
    assert _names(poller.ingest_window(tmp_path)) == ["20260330.tar.gz", "20260331.tar.gz", "20260401.tar.gz"]


def test_the_window_starts_at_the_tarball_the_marker_names(tmp_path):
    _tarballs(tmp_path, "20260325", "20260326", "20260327", "20260331", "20260401")
    poller.mark_ingested_through(tmp_path, "20260327")
    assert _names(poller.ingest_window(tmp_path)) == ["20260327.tar.gz", "20260331.tar.gz", "20260401.tar.gz"]


def test_nothing_is_read_when_no_tarball_is_newer_than_the_marker(tmp_path):
    _tarballs(tmp_path, "20260331", "20260401")
    poller.mark_ingested_through(tmp_path, "20260401")
    assert poller.ingest_window(tmp_path) == []
    assert poller.ingested_through(tmp_path) == "20260401"


# --- fetching ---------------------------------------------------------------


def test_a_fetch_goes_through_the_ssrf_guard_with_a_cap():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"pb"
    with patch("pipeline.poller.safe_urlopen", return_value=response) as opened:
        assert poller.fetch_feed("https://feed.example/1.pb") == b"pb"
    opened.assert_called_once_with(
        "https://feed.example/1.pb", timeout=poller.FETCH_TIMEOUT_SEC, max_bytes=MAX_POLL_BYTES
    )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/feed.pb", "http://169.254.169.254/"])
def test_a_feed_url_that_is_not_public_http_is_refused(url):
    with pytest.raises(FeedURLError):
        poller.fetch_feed(url)


def test_one_slow_feed_does_not_hold_back_the_others(tmp_path):
    second_fetched = threading.Event()

    def fetch(url):
        if url.endswith("/1.pb"):
            assert second_fetched.wait(5), "the second feed waited for the first"
        else:
            second_fetched.set()
        return b"poll"

    agencies = [(1, "https://feed.example/1.pb"), (2, "https://feed.example/2.pb")]
    p = _poller(tmp_path, agencies=agencies, fetch=fetch)
    with patch("pipeline.poller.ingest_pushed_poll", return_value=1):
        p.poll_cycle()
    assert p.pushed == {1: 1, 2: 1}


# --- polling ----------------------------------------------------------------


def test_each_poll_is_archived_then_pushed_under_its_member_name(tmp_path):
    p = _poller(tmp_path)
    seen = []

    def push(agency_id, raw, captured_at, source_file, conn, ch_client):
        assert (tmp_path / "1" / "rt" / source_file).read_bytes() == raw
        seen.append((agency_id, source_file, captured_at))
        return 1

    with patch("pipeline.poller.ingest_pushed_poll", side_effect=push):
        p.poll_cycle()

    assert seen == [(1, "20260402/TripUpdate_100000.pb", "2026-04-02T10:00:00+00:00")]
    assert p.pushed[1] == 1


def test_a_fetch_error_is_counted_and_the_loop_keeps_polling(tmp_path, caplog):
    stop = threading.Event()
    calls = Counter()
    lock = threading.Lock()

    def fetch(url):
        with lock:
            calls[url] += 1
            if sum(calls.values()) >= 6:
                stop.set()
        if "broken" in url:
            raise OSError(f"connection refused fetching {url}")
        return b"poll"

    agencies = [(1, "https://broken.example/feed?key=SECRET"), (2, "https://feed.example/2.pb")]
    p = _poller(tmp_path, agencies=agencies, fetch=fetch, interval_sec=0.001, stop=stop)
    with patch("pipeline.poller.ingest_pushed_poll", return_value=1) as push:
        p.run()

    assert p.fetch_failures[1] == 3
    assert p.pushed[2] == 3
    assert push.call_count == 3
    assert "SECRET" not in caplog.text


def test_a_failed_push_is_counted_and_the_next_agency_still_polled(tmp_path):
    p = _poller(tmp_path, agencies=[(1, "https://feed.example/1.pb"), (2, "https://feed.example/2.pb")])
    with patch("pipeline.poller.ingest_pushed_poll", side_effect=[RuntimeError("clickhouse down"), 1]):
        p.poll_cycle()
    assert p.push_failures[1] == 1
    assert p.pushed[2] == 1
    assert (tmp_path / "1" / "rt" / "20260402" / "TripUpdate_100000.pb").exists()


def test_with_clickhouse_down_every_poll_is_still_archived(tmp_path):
    agencies = [(1, "https://feed.example/1.pb"), (2, "https://feed.example/2.pb")]
    p = _poller(tmp_path, stores=_stores(agencies, clickhouse_up=False))
    with patch("pipeline.poller.ingest_pushed_poll") as push:
        p.poll_cycle()
    push.assert_not_called()
    for aid in (1, 2):
        assert (tmp_path / str(aid) / "rt" / "20260402" / "TripUpdate_100000.pb").read_bytes() == b"poll"
    assert p.push_failures == {1: 1, 2: 1}


def test_with_postgres_down_the_last_roster_is_still_archived(tmp_path):
    p = _poller(tmp_path)
    with patch("pipeline.poller.ingest_pushed_poll", return_value=1):
        p.poll_cycle()
    p._stores = _postgres_down()
    p._clock.now += timedelta(minutes=1)
    p.poll_cycle()
    assert (tmp_path / "1" / "rt" / "20260402" / "TripUpdate_100100.pb").exists()
    assert p.push_failures[1] == 1


def test_with_postgres_down_from_the_start_nothing_is_polled(tmp_path):
    p = _poller(tmp_path, stores=_postgres_down(), fetch=lambda url: pytest.fail("fetched with no roster"))
    p.poll_cycle()


def test_sigterm_stops_the_loop():
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        poller.install_stop_handlers(stop)
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    finally:
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])


# --- the daily step's schedule ----------------------------------------------


def _scheduled(tmp_path, outcomes, agencies=(1, 2)):
    for aid in agencies:
        (tmp_path / str(aid) / "rt").mkdir(parents=True)
    clock = _Clock(_utc(2026, 4, 2, 10, 0, 0))
    plans: list[dict] = []

    def daily(root, plan):
        plans.append({aid: set(stages) for aid, stages in plan.items()})
        return outcomes.pop(0) if outcomes else {}

    return _poller(tmp_path, clock=clock, daily_step=daily), clock, plans


def _step(p) -> str | None:
    kind = p.maybe_start_daily()
    p.join_background()
    return kind


def test_the_utc_day_step_runs_at_start_then_once_per_utc_day(tmp_path):
    p, clock, plans = _scheduled(tmp_path, [])
    assert _step(p) == "utc"
    clock.now = _utc(2026, 4, 2, 14, 59, 59)
    assert _step(p) is None
    clock.now = _utc(2026, 4, 3, 0, 0, 1)
    assert _step(p) == "utc"
    full = {"promote", "ingest", "analyze"}
    assert plans == [{1: full, 2: full}, {1: full, 2: full}]


def test_jst_midnight_promotes_and_analyzes_without_ingesting(tmp_path):
    p, clock, plans = _scheduled(tmp_path, [])
    _step(p)
    clock.now = _utc(2026, 4, 2, 15, 0, 1)
    assert _step(p) == "jst"
    clock.now = _utc(2026, 4, 2, 23, 0, 0)
    assert _step(p) is None
    assert plans[-1] == {1: {"promote", "analyze"}, 2: {"promote", "analyze"}}


def test_only_the_failed_stages_are_retried_with_what_follows_them(tmp_path):
    p, clock, plans = _scheduled(tmp_path, [{2: {"ingest"}}, {}])
    _step(p)
    assert p.pending_retry == {2: {"ingest"}}
    clock.now += timedelta(seconds=poller.DAILY_RETRY_SEC - 1)
    assert _step(p) is None
    clock.now += timedelta(seconds=1)
    assert _step(p) == "retry"
    assert plans[-1] == {2: {"ingest", "analyze"}}
    assert p.pending_retry == {}


def test_retries_stop_at_the_daily_cap(tmp_path):
    always_failing = [{1: {"promote"}}] * (poller.MAX_RETRIES_PER_DAY + 2)
    p, clock, plans = _scheduled(tmp_path, list(always_failing), agencies=(1,))
    _step(p)
    for _ in range(poller.MAX_RETRIES_PER_DAY + 1):
        clock.now += timedelta(seconds=poller.DAILY_RETRY_SEC)
        _step(p)
    assert sum(1 for plan in plans[1:]) == poller.MAX_RETRIES_PER_DAY
    assert p.pending_retry == {}


def test_a_step_still_running_is_not_started_twice(tmp_path):
    release = threading.Event()

    def slow(root, plan):
        release.wait(5)
        return {}

    (tmp_path / "1" / "rt").mkdir(parents=True)
    p = _poller(tmp_path, daily_step=slow)
    assert p.maybe_start_daily() == "utc"
    assert p.maybe_start_daily() is None
    release.set()
    p.join_background()


def test_closed_days_are_packed_before_the_step_reads_them(tmp_path):
    rt = tmp_path / "1" / "rt"
    _day_dir(rt, "20260401", ["120000"])
    seen = []
    p = _poller(tmp_path, daily_step=lambda root, plan: seen.append((rt / "20260401.tar.gz").exists()) or {})
    _step(p)
    assert seen == [True]


def test_a_pack_failure_still_runs_the_step_and_is_retried(tmp_path):
    p, clock, plans = _scheduled(tmp_path, [], agencies=(1, 2))
    with patch("pipeline.poller.pack_closed_days", side_effect=lambda rt, today: _fail_for(rt, "1")):
        _step(p)
    assert plans == [{1: {"promote", "ingest", "analyze"}, 2: {"promote", "ingest", "analyze"}}]
    assert p.pending_retry == {1: {"pack"}}
    clock.now += timedelta(seconds=poller.DAILY_RETRY_SEC)
    _step(p)
    assert plans[-1] == {1: {"ingest", "analyze"}}


def _fail_for(rt: Path, agency: str) -> list:
    if rt.parent.name == agency:
        raise OSError("disk full")
    return []


def test_a_step_that_raises_is_retried_whole(tmp_path):
    def broken(root, plan):
        raise OSError("could not connect")

    (tmp_path / "1" / "rt").mkdir(parents=True)
    p = _poller(tmp_path, daily_step=broken)
    _step(p)
    assert p.pending_retry == {1: {"promote", "ingest", "analyze"}}


def test_a_missing_timetable_owes_no_retry_but_a_failure_does():
    from pipeline.ingest import StaticTimetableMissing

    def no_timetable():
        raise StaticTimetableMissing(1, "static_join")

    def broken():
        raise RuntimeError("clickhouse down")

    with patch("pipeline.runs.record_run", _run_row):
        assert poller._stage(MagicMock(), "ingest", 1, no_timetable) == poller.SKIPPED
        assert poller._stage(MagicMock(), "ingest", 1, broken) == poller.FAILED
        assert poller._stage(MagicMock(), "analyze", 1, lambda: None) == poller.OK


@contextmanager
def _run_row(conn, kind, agency_id=None):
    yield MagicMock()


def test_the_marker_moves_only_when_ingest_succeeds(tmp_path):
    rt = tmp_path / "1" / "rt"
    _tarballs(rt, "20260331", "20260401")
    with patch("pipeline.poller._stage", return_value=poller.FAILED):
        assert poller._ingest_stage(MagicMock(), MagicMock(), tmp_path, 1) == poller.FAILED
    assert poller.ingested_through(rt) is None
    with patch("pipeline.poller._stage", return_value=poller.OK):
        poller._ingest_stage(MagicMock(), MagicMock(), tmp_path, 1)
    assert poller.ingested_through(rt) == "20260401"


# --- the coverage re-probe ---------------------------------------------------


def test_the_re_probe_runs_at_start_then_hourly(tmp_path):
    runs = []
    clock = _Clock(_utc(2026, 4, 2, 10, 0, 0))
    p = _poller(tmp_path, clock=clock, reprobe=lambda: runs.append(clock.now))
    assert p.maybe_start_reprobe()
    p.join_background()
    clock.now += timedelta(seconds=poller.REPROBE_INTERVAL_SEC - 1)
    assert not p.maybe_start_reprobe()
    clock.now += timedelta(seconds=1)
    assert p.maybe_start_reprobe()
    p.join_background()
    assert len(runs) == 2


def test_a_failing_re_probe_does_not_stop_the_loop(tmp_path):
    def broken():
        raise RuntimeError("feed down")

    p = _poller(tmp_path, reprobe=broken)
    p.maybe_start_reprobe()
    p.join_background()
