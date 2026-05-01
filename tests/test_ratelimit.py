import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from api.middleware.ratelimit import limiter, FREE_LIMIT, PRO_LIMIT, _key_func


def test_free_limit_constant():
    assert FREE_LIMIT == "60/minute"


def test_pro_limit_constant():
    assert PRO_LIMIT == "600/minute"


def test_key_func_free_tier_uses_ip():
    from unittest.mock import MagicMock
    request = MagicMock()
    request.state.tier = "free"
    request.headers = {}
    request.client.host = "1.2.3.4"
    # get_remote_address returns client.host for non-proxied requests
    # We just test that it doesn't use "pro:" prefix for free tier
    key = _key_func(request)
    assert not key.startswith("pro:")


def test_key_func_pro_tier_uses_api_key():
    from unittest.mock import MagicMock
    request = MagicMock()
    request.state.tier = "pro"
    request.headers.get = lambda k, default=None: "my-api-key" if k == "X-API-Key" else default
    key = _key_func(request)
    assert key == "pro:my-api-key"
