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


async def _raw_asgi_post(headers: dict[str, str], body_chunks: list[bytes]) -> int:
    """Drive the app at the ASGI layer and return the response status.

    The two cases below need a Content-Length that disagrees with the bytes
    actually sent, which an HTTP client will not produce -- it recomputes the
    header. Speaking ASGI directly is the only way to pin these paths.
    """
    app = _build_app()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/internal/collector/updates/1",
        "raw_path": b"/internal/collector/updates/1",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    pending = list(body_chunks)

    async def receive():
        if pending:
            return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}
        return {"type": "http.request", "body": b"", "more_body": False}

    status = {}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await app(scope, receive, send)
    return status["code"]


async def test_non_numeric_content_length_still_bounded_by_the_stream():
    """A Content-Length that isn't a number falls through to the streaming
    cap rather than skipping the check."""
    oversize = b"x" * (_MAX_COLLECTOR_PAYLOAD + 1)
    code = await _raw_asgi_post(
        {**VALID_HEADERS, "content-length": "not-a-number"},
        [oversize],
    )
    assert code == 413


async def test_understated_content_length_still_bounded_by_the_stream():
    """A header that understates the body must not license an unbounded read:
    the per-chunk check is what actually holds here."""
    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * ((_MAX_COLLECTOR_PAYLOAD // len(chunk)) + 2)
    code = await _raw_asgi_post(
        {**VALID_HEADERS, "content-length": "100"},
        chunks,
    )
    assert code == 413


async def test_honest_small_content_length_is_accepted():
    """The negative control for the two above: the same raw path accepts a
    normal collector request, so a 413 there means the cap fired, not that
    the request was malformed."""
    with patch("api.routers.internal._ingest_collector_payload", return_value=1):
        code = await _raw_asgi_post(
            {**VALID_HEADERS, "content-length": "14"},
            [b"protobuf-bytes"],
        )
    assert code == 200
