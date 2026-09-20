import logging
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.logging_config import REQUEST_ID_CTX, configure
from api.middleware.request_log import RequestLogMiddleware, _escape_for_log, _resolve_request_id


def test_request_id_filter_default_dash():
    """A LogRecord emitted outside any request scope must still resolve
    request_id (to '-') so the format string never KeyErrors."""
    configure()
    logger = logging.getLogger("test.outside_request")
    rec = logger.makeRecord(
        name="test.outside_request",
        level=logging.INFO,
        fn="t",
        lno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    root = logging.getLogger()
    for f in root.handlers[0].filters:
        f.filter(rec)  # type: ignore[union-attr]
    assert rec.request_id == "-"  # type: ignore[attr-defined]


def test_request_id_filter_reads_contextvar():
    configure()
    token = REQUEST_ID_CTX.set("ctx-test")
    try:
        rec = logging.getLogger("test").makeRecord(
            "test",
            logging.INFO,
            "t",
            1,
            "hi",
            (),
            None,
        )
        for f in logging.getLogger().handlers[0].filters:
            f.filter(rec)  # type: ignore[union-attr]
        assert rec.request_id == "ctx-test"  # type: ignore[attr-defined]
    finally:
        REQUEST_ID_CTX.reset(token)


def test_configure_is_idempotent():
    """Calling configure() twice doesn't pile up root handlers."""
    configure()
    n1 = len(logging.getLogger().handlers)
    configure()
    n2 = len(logging.getLogger().handlers)
    assert n2 == n1


def test_resolve_request_id_accepts_valid():
    assert _resolve_request_id([(b"x-request-id", b"smoke-001")]) == "smoke-001"


def test_resolve_request_id_rejects_newline_injection():
    out = _resolve_request_id([(b"x-request-id", b"abc\ninject")])
    assert out != "abc\ninject"
    assert re.fullmatch(r"[a-f0-9]{32}", out)


def test_resolve_request_id_rejects_oversize():
    out = _resolve_request_id([(b"x-request-id", b"a" * 65)])
    assert out != "a" * 65
    assert len(out) == 32


def test_resolve_request_id_generates_when_missing():
    out = _resolve_request_id([])
    assert re.fullmatch(r"[a-f0-9]{32}", out)


@pytest.mark.asyncio
async def test_middleware_echoes_client_request_id():
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(RequestLogMiddleware)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ping", headers={"X-Request-Id": "echo-test"})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "echo-test"


@pytest.mark.asyncio
async def test_middleware_generates_request_id_when_absent():
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(RequestLogMiddleware)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ping")
    assert re.fullmatch(r"[a-f0-9]{32}", r.headers["x-request-id"])


@pytest.mark.asyncio
async def test_access_log_emitted_with_kv_fields(caplog):
    configure()
    # configure() reset the root handlers, so re-attach caplog's so it can
    # capture the access log line the middleware emits.
    logging.getLogger().addHandler(caplog.handler)
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(RequestLogMiddleware)

    with caplog.at_level(logging.INFO, logger="api.access"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.get("/ping", headers={"X-Request-Id": "log-shape-001"})

    access = [r for r in caplog.records if r.name == "api.access"]
    assert len(access) == 1
    msg = access[0].getMessage()
    assert "method=GET" in msg
    assert 'path="/ping"' in msg
    assert "status=200" in msg
    assert "duration_ms=" in msg
    assert "user_id=-" in msg


@pytest.mark.asyncio
async def test_concurrent_requests_dont_bleed_request_id():
    """Two concurrent requests with distinct X-Request-Id headers must
    each get their own value back. Pins spec goal #5 (contextvars
    isolation per asyncio task)."""
    import asyncio

    app = FastAPI()

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.05)
        return {"ok": True}

    app.add_middleware(RequestLogMiddleware)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r1, r2 = await asyncio.gather(
            c.get("/slow", headers={"X-Request-Id": "concurrent-A"}),
            c.get("/slow", headers={"X-Request-Id": "concurrent-B"}),
        )
    assert r1.headers["x-request-id"] == "concurrent-A"
    assert r2.headers["x-request-id"] == "concurrent-B"


@pytest.mark.asyncio
async def test_access_log_renders_status_question_mark_on_early_failure(caplog):
    """If the app raises before any http.response.start, status=0 is
    confusing — render it as ? so the operator can tell it apart from
    a real status code."""
    configure()
    logging.getLogger().addHandler(caplog.handler)
    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    app.add_middleware(RequestLogMiddleware)

    with caplog.at_level(logging.INFO, logger="api.access"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            try:
                await c.get("/boom")
            except Exception:
                pass

    access = [r for r in caplog.records if r.name == "api.access"]
    assert len(access) >= 1
    # ServerErrorMiddleware may catch and emit 500 BEFORE our outermost
    # middleware sees the response — accept either status=500 or
    # status=? depending on Starlette version.
    msg = access[-1].getMessage()
    assert "status=500" in msg or "status=?" in msg


@pytest.mark.asyncio
async def test_access_log_includes_user_id_when_authenticated(caplog):
    """request.state.user.user_id must surface in the access log.
    Starlette stores state writes in scope['state'] as a dict — pin the
    middleware's user_id resolution against that."""
    from types import SimpleNamespace

    configure()
    logging.getLogger().addHandler(caplog.handler)
    app = FastAPI()

    @app.middleware("http")
    async def attach_user(request, call_next):
        request.state.user = SimpleNamespace(user_id=42)
        return await call_next(request)

    @app.get("/me")
    async def me():
        return {"ok": True}

    app.add_middleware(RequestLogMiddleware)

    with caplog.at_level(logging.INFO, logger="api.access"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.get("/me")

    access = [r for r in caplog.records if r.name == "api.access"]
    assert len(access) == 1
    assert "user_id=42" in access[0].getMessage()


def test_escape_for_log_quotes_a_plain_value():
    assert _escape_for_log("/agencies/1/routes") == '"/agencies/1/routes"'


def test_escape_for_log_escapes_embedded_quotes_and_backslashes():
    assert _escape_for_log('/x"evil"') == '"/x\\"evil\\""'
    assert _escape_for_log("/x\\evil") == '"/x\\\\evil"'


def test_escape_for_log_escapes_control_characters():
    assert _escape_for_log("/x\nevil") == '"/x\\x0aevil"'
    assert _escape_for_log("/x\revil") == '"/x\\x0devil"'


@pytest.mark.asyncio
async def test_access_log_escapes_crafted_path_instead_of_forging_fields(caplog):
    """The request path is attacker-controlled (it's the percent-decoded
    URL). Logged unescaped inside the space-delimited `key=value` access
    line, a path containing `"`, a space, and a newline could forge extra
    fields or an entirely separate fake log line. Quoting and escaping it
    keeps the crafted content inertly inside the one `path="..."` field."""
    configure()
    logging.getLogger().addHandler(caplog.handler)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list = []

    async def send(message):
        sent.append(message)

    middleware = RequestLogMiddleware(app)
    crafted_path = '/x" status=999 fake_field="pwned\ninjected'
    scope = {"type": "http", "method": "GET", "path": crafted_path, "headers": [], "state": {}}

    with caplog.at_level(logging.INFO, logger="api.access"):
        await middleware(scope, receive, send)

    access = [r for r in caplog.records if r.name == "api.access"]
    assert len(access) == 1
    msg = access[0].getMessage()
    assert "\n" not in msg
    assert f"path={_escape_for_log(crafted_path)}" in msg
    assert "status=200" in msg  # the real status, appended after the quoted path


def test_escape_for_log_keeps_japanese_readable():
    """Routes here legitimately carry Japanese, and escaping every non-ASCII
    character would render an ordinary path unreadable in the access log —
    as ``\\xNNNN`` at that, since a CJK codepoint doesn't fit the two hex
    digits the format implies. Nothing above U+00A0 can close the quoted
    field or start a new line, so there is nothing to defend against."""
    assert _escape_for_log("/api/1/青森") == '"/api/1/青森"'


def test_escape_for_log_still_neutralises_control_characters():
    """The injection guard itself: a newline must not be able to end the
    field and forge a second log line."""
    assert _escape_for_log("/x\nstatus=200") == '"/x\\x0astatus=200"'
    assert _escape_for_log("/x\tsep") == '"/x\\x09sep"'
    assert _escape_for_log("/x\x85next") == '"/x\\x85next"'
