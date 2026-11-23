"""`/health/ready` answers 200 only when both databases answer, and answers
promptly whichever of them hangs, so an uptime monitor outside the machine
can alert on it."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main
from api.main import app


class _Pool:
    def __init__(self, fetchval):
        self.fetchval = fetchval


class _Ch:
    def __init__(self, command):
        self.command = command


async def _answer(_sql):
    return 1


async def _refuse(_sql):
    raise ConnectionRefusedError("refused")


async def _hang(_sql):
    await asyncio.Event().wait()


@pytest.fixture
def stores(monkeypatch):
    def install(pg, ch):
        monkeypatch.setattr(app.state, "pool", _Pool(pg), raising=False)
        monkeypatch.setattr(app.state, "ch_client", _Ch(ch) if ch is not None else None, raising=False)
        return TestClient(app)

    monkeypatch.setattr(api.main, "READY_PROBE_TIMEOUT_SECONDS", 0.1)
    return install


def test_both_databases_answering_is_ready(stores):
    response = stores(_answer, _answer).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "postgres": "ok", "clickhouse": "ok"}


@pytest.mark.parametrize(
    ("pg", "ch", "body"),
    [
        (_refuse, _answer, {"status": "unavailable", "postgres": "down", "clickhouse": "ok"}),
        (_hang, _answer, {"status": "unavailable", "postgres": "down", "clickhouse": "ok"}),
        (_answer, _refuse, {"status": "unavailable", "postgres": "ok", "clickhouse": "down"}),
        (_answer, _hang, {"status": "unavailable", "postgres": "ok", "clickhouse": "down"}),
        (_hang, _hang, {"status": "unavailable", "postgres": "down", "clickhouse": "down"}),
    ],
)
def test_a_database_that_fails_or_hangs_is_named(stores, pg, ch, body):
    response = stores(pg, ch).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == body


@pytest.mark.parametrize(("ch", "status"), [(_answer, 200), (_refuse, 503)])
def test_a_head_probe_gets_the_same_status(stores, ch, status):
    response = stores(_answer, ch).head("/health/ready")
    assert response.status_code == status


def test_a_session_cookie_does_not_reach_the_database(stores):
    # The stub pool has no fetchrow, so a session lookup would fail the request.
    client = stores(_answer, _answer)
    client.cookies.set("sid", "a-session")
    assert client.get("/health/ready").status_code == 200


def test_clickhouse_unreachable_at_startup_is_down(stores):
    # The app opens its ClickHouse client only at startup and keeps None when
    # that fails.
    response = stores(_answer, None).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "postgres": "ok", "clickhouse": "down"}


def test_the_two_probes_run_at_once(stores, monkeypatch):
    # Each probe waits for the other to start, so only probes run side by side
    # both finish; one after the other, the first times out.
    monkeypatch.setattr(api.main, "READY_PROBE_TIMEOUT_SECONDS", 2.0)
    pg_started, ch_started = asyncio.Event(), asyncio.Event()

    async def pg(_sql):
        pg_started.set()
        await ch_started.wait()

    async def ch(_sql):
        ch_started.set()
        await pg_started.wait()

    response = stores(pg, ch).get("/health/ready")
    assert response.status_code == 200
