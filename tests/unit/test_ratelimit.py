from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, _key_func, limiter, tier_limit


def test_free_limit_constant():
    assert FREE_LIMIT == "60/minute"


def test_pro_limit_constant():
    assert PRO_LIMIT == "600/minute"


def test_key_func_free_tier_uses_ip():
    request = MagicMock()
    request.state.tier = "free"
    request.headers = {}
    request.client.host = "1.2.3.4"
    # get_remote_address returns client.host for non-proxied requests
    # We just test that it doesn't use "pro:" prefix for free tier
    key = _key_func(request)
    assert not key.startswith("pro:")


def test_key_func_pro_tier_uses_api_key():
    request = MagicMock()
    request.state.tier = "pro"
    request.headers.get = lambda k, default=None: "my-api-key" if k == "X-API-Key" else default
    key = _key_func(request)
    assert key == "pro:my-api-key"


def _tiered_app() -> TestClient:
    """One route behind `tier_limit`, with the tier taken from a test header
    the way `APIKeyMiddleware` would set it from a real key row."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    @app.middleware("http")
    async def _tier_from_header(request: Request, call_next):
        request.state.tier = request.headers.get("X-Test-Tier", "free")
        return await call_next(request)

    @app.get("/ping")
    @limiter.limit(tier_limit)
    async def ping(request: Request):
        return {"ok": True}

    return TestClient(app)


# Built once: slowapi keys registered limits by `module.function`, so a second
# registration of `ping` would make every request count twice.
_TIERED = _tiered_app()


def test_tier_limit_picks_one_limit_from_the_key_prefix():
    assert tier_limit("pro:some-key") == PRO_LIMIT
    assert tier_limit("203.0.113.9") == FREE_LIMIT


def test_a_pro_key_survives_request_61():
    limiter.reset()
    headers = {"X-Test-Tier": "pro", "X-API-Key": "k-a1-pro"}
    statuses = [_TIERED.get("/ping", headers=headers).status_code for _ in range(61)]
    assert statuses == [200] * 61


def test_a_free_caller_is_cut_off_at_request_61():
    limiter.reset()
    statuses = [_TIERED.get("/ping").status_code for _ in range(61)]
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429


def test_an_unknown_tier_string_is_metered_as_free():
    """A legacy row can carry any `tier` text; only `pro` buys the pro bucket."""
    request = MagicMock()
    request.state.tier = "enterprise"
    request.headers.get = lambda k, default=None: "legacy-key" if k == "X-API-Key" else default
    request.client.host = "198.51.100.7"
    assert tier_limit(_key_func(request)) == FREE_LIMIT


def test_no_route_still_stacks_both_limits_in_one_string():
    api_dir = Path(__file__).resolve().parents[2] / "api"
    offenders = sorted(
        str(p.relative_to(api_dir)) for p in api_dir.rglob("*.py") if "{FREE_LIMIT};{PRO_LIMIT}" in p.read_text()
    )
    assert offenders == [], f"these routes still pass both limits to slowapi: {offenders}"
