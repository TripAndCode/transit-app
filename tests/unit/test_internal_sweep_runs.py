"""The cron sweep's bookkeeping: one row per action, and no stray fetches.

`_ingest_and_analyze_sweep` serves three callers with different contracts —
the cron poke, the board's manual trigger, and the agency drawer's scoped
re-analyze — and the differences between them are invisible in the function
body. Pinned here:

- a caller that already owns a run row is never given a second one, so an
  operator's timeline shows one bar per button press;
- the fleet-wide weather fetch belongs to the scheduled sweep only;
- the error an operator reads off a bar never carries a feed credential.

DB-free and network-free: every boundary the sweep reaches for (psycopg2,
ClickHouse, the advisory lock, ingest/analyze, the weather pass) is replaced
with a recorder.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg2
import pytest

import pipeline.analyze
import pipeline.clickhouse
import pipeline.freshness
import pipeline.ingest
from api.routers import internal
from pipeline import runs as pipeline_runs

_DB_URL = "postgresql://stub/stub"


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), params))
        self._rows = [(aid,) for aid in self.conn.agency_ids] if "FROM agencies" in sql else []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, agency_ids):
        self.agency_ids = list(agency_ids)
        self.statements: list[tuple[str, object]] = []
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _ChClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Env:
    """What the sweep did, seen from every boundary it was cut at."""

    def __init__(self):
        self.got_lock = True
        self.started: list[tuple] = []
        self.recorded: list[tuple] = []
        self.weather: list[str] = []
        self.conn = _Conn([1])


@pytest.fixture
def sweep(monkeypatch) -> _Env:
    env = _Env()

    monkeypatch.setattr(psycopg2, "connect", lambda _url: env.conn)
    monkeypatch.setattr(pipeline.clickhouse, "get_client", _ChClient)
    monkeypatch.setattr(internal, "try_lock_ingest_analyze_timed", lambda _conn: (env.got_lock, 4))
    monkeypatch.setattr(pipeline.ingest, "ingest_live", lambda *_a, **_k: 0)
    monkeypatch.setattr(pipeline.analyze, "analyze", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline.freshness, "check_agg_freshness", lambda *_a, **_k: [])
    monkeypatch.setattr(internal, "_run_weather_ingest", lambda db_url: env.weather.append(db_url))

    def _start_run(_conn, kind, **kwargs):
        env.started.append((kind, kwargs.get("status", "running")))
        return 1

    @contextmanager
    def _record_run(_conn, kind, **kwargs):
        env.recorded.append((kind, kwargs.get("agency_id")))
        yield pipeline_runs.RunHandle(run_id=1)

    monkeypatch.setattr(pipeline_runs, "start_run", _start_run)
    monkeypatch.setattr(pipeline_runs, "record_run", _record_run)
    return env


def test_a_displaced_cron_sweep_records_the_run_it_lost(sweep):
    """A skipped poke's row is the only evidence a scheduled job was
    displaced; nothing else in the system keeps it."""
    sweep.got_lock = False
    assert internal._ingest_and_analyze_sweep(_DB_URL) == "skipped"
    assert sweep.started == [("ingest", "skipped")]


def test_a_displaced_sweep_with_a_caller_row_does_not_open_a_second_one(sweep):
    """The operator's umbrella row is closed as `skipped` by the caller, so
    opening another here would draw two bars for one button press."""
    sweep.got_lock = False
    assert internal._ingest_and_analyze_sweep(_DB_URL, run_id=77) == "skipped"
    assert sweep.started == []


def test_the_scheduled_sweep_still_drives_the_fleet_weather_pass(sweep):
    assert internal._ingest_and_analyze_sweep(_DB_URL) == "ok"
    assert sweep.weather == [_DB_URL]


def test_a_sweep_told_not_to_fetch_weather_leaves_the_third_party_alone(sweep):
    assert internal._ingest_and_analyze_sweep(_DB_URL, run_weather=False) == "ok"
    assert sweep.weather == []


def test_the_runner_passes_its_weather_decision_through_to_the_sweep(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    monkeypatch.setattr(internal, "_ingest_and_analyze_sweep", lambda _url, **kwargs: seen.append(kwargs) or "ok")
    monkeypatch.setattr(internal, "_finish_manual_run", lambda *_a, **_k: None)

    internal._run_ingest_and_analyze(run_weather=False, run_id=5)
    assert seen[0]["run_weather"] is False
    assert seen[0]["run_id"] == 5


def test_a_failed_sweep_stores_an_error_without_the_feed_credential(monkeypatch):
    closed: list[tuple] = []
    monkeypatch.setenv("DATABASE_URL", _DB_URL)

    def _boom(*_a, **_k):
        raise RuntimeError("fetch failed: https://u:p@feeds.test/rt.pb?apikey=SECRET")

    monkeypatch.setattr(internal, "_ingest_and_analyze_sweep", _boom)
    monkeypatch.setattr(
        internal,
        "_finish_manual_run",
        lambda db_url, run_id, status, error=None: closed.append((run_id, status, error)),
    )

    internal._run_ingest_and_analyze(run_id=5)
    run_id, status, error = closed[0]
    assert (run_id, status) == (5, "error")
    assert "SECRET" not in error
    assert "u:p" not in error
    assert "https://feeds.test/rt.pb" in error
