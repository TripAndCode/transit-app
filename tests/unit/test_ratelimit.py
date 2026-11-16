from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.middleware.ratelimit import DEFAULT_LIMIT, limiter


def test_default_limit_constant():
    assert DEFAULT_LIMIT == "60/minute"


def _app() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    @app.get("/ping")
    @limiter.limit(DEFAULT_LIMIT)
    async def ping(request: Request):
        return {"ok": True}

    return TestClient(app)


# Built once: slowapi keys registered limits by `module.function`, so a second
# registration of `ping` would make every request count twice.
_APP = _app()


def test_every_caller_shares_one_limit_keyed_on_address():
    """An arbitrary X-API-Key does not buy a different bucket: the limiter
    keys on the client address alone, so 60 requests from one address are
    the cap no matter what headers ride along."""
    limiter.reset()
    statuses = [_APP.get("/ping", headers={"X-API-Key": f"k-{i}"}).status_code for i in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


def test_a_caller_with_no_header_is_cut_off_at_request_61():
    limiter.reset()
    statuses = [_APP.get("/ping").status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
