"""Unit tests for POST /internal/collector/updates/{agency_id}.

DB-free: mounts only ``collector_router`` on a bare FastAPI app instead of the
full ``api.main`` app, and mocks ``_ingest_collector_payload`` (which opens its
own psycopg2/ClickHouse connections) rather than touching a real database.
"""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from starlette.requests import Request as StarletteRequest

from api.routers.internal import _MAX_COLLECTOR_PAYLOAD, collector_router

VALID_HEADERS = {
    "X-Collector-Secret": "shh",
    "X-Source-File": "20260919/TripUpdate_120000.pb",
    "X-Captured-At": "2026-09-19T12:00:00+00:00",
}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(collector_router)
    return app


@pytest.fixture
def client():
    app = _build_app()
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def collector_secret(monkeypatch):
    monkeypatch.setenv("COLLECTOR_INGEST_SECRET", "shh")


async def test_missing_secret_rejected(client):
    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers={k: v for k, v in VALID_HEADERS.items() if k != "X-Collector-Secret"},
            content=b"data",
        )
    assert r.status_code == 401


async def test_wrong_secret_rejected(client):
    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers={**VALID_HEADERS, "X-Collector-Secret": "nope"},
            content=b"data",
        )
    assert r.status_code == 401


async def test_content_length_over_cap_rejected_without_reading_body(client, monkeypatch):
    stream_consumed = False
    original_stream = StarletteRequest.stream

    async def spy_stream(self):
        nonlocal stream_consumed
        stream_consumed = True
        async for chunk in original_stream(self):
            yield chunk

    monkeypatch.setattr(StarletteRequest, "stream", spy_stream)

    oversize = b"x" * (_MAX_COLLECTOR_PAYLOAD + 1)
    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers=VALID_HEADERS,
            content=oversize,
        )
    assert r.status_code == 413
    assert stream_consumed is False


async def test_chunked_body_over_cap_rejected(client):
    async def oversize_chunks():
        chunk = b"x" * 1024
        for _ in range(_MAX_COLLECTOR_PAYLOAD // 1024 + 2):
            yield chunk

    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers=VALID_HEADERS,
            content=oversize_chunks(),
        )
    assert r.status_code == 413


async def test_missing_timezone_in_captured_at_rejected(client):
    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers={**VALID_HEADERS, "X-Captured-At": "2026-09-19T12:00:00"},
            content=b"data",
        )
    assert r.status_code == 400
    assert "timezone" in r.json()["detail"].lower()


async def test_bad_source_file_name_rejected(client):
    async with client as ac:
        r = await ac.post(
            "/internal/collector/updates/1",
            headers={**VALID_HEADERS, "X-Source-File": "not-a-valid-name.pb"},
            content=b"data",
        )
    assert r.status_code == 400
    assert "X-Source-File" in r.json()["detail"]


async def test_happy_path_calls_ingest_once(client):
    with patch("api.routers.internal._ingest_collector_payload", return_value=42) as mock_ingest:
        async with client as ac:
            r = await ac.post(
                "/internal/collector/updates/1",
                headers=VALID_HEADERS,
                content=b"protobuf-bytes",
            )
    assert r.status_code == 200
    assert r.json() == {"status": "accepted", "inserted": 42}
    mock_ingest.assert_called_once()
    args = mock_ingest.call_args.args
    assert args[0] == 1
    assert args[1] == b"protobuf-bytes"
    assert args[3] == "oracle/20260919/TripUpdate_120000.pb"
