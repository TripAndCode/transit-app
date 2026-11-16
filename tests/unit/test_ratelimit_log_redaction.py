"""The slowapi rate-limit warning must never carry a client address.

Drives a real slowapi emission (not a hand-written LogRecord) through a
minimal FastAPI app, so a slowapi upgrade that changes the warning's format
string shows up as a real test failure rather than silently stopping the
filter from matching.
"""

import io
import logging

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.testclient import TestClient

from api.logging_config import configure


def _build_app() -> FastAPI:
    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.get("/limited")
    @limiter.limit("1/minute")
    def limited(request: Request):
        return {"ok": True}

    return app


def test_rate_limit_warning_omits_the_client_address():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    configure()
    stream = io.StringIO()
    for handler in root.handlers:
        handler.stream = stream

    try:
        app = _build_app()
        with TestClient(app) as client:
            assert client.get("/limited").status_code == 200
            assert client.get("/limited").status_code == 429
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)

    output = stream.getvalue()
    assert "exceeded" in output
    assert "testclient" not in output
