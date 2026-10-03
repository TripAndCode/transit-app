"""Unit tests for ``api.security.csrf_guard`` itself (no DB, no endpoint).

Exercises the guard directly against bare ``Request`` objects so the
Origin/Referer allow-list behavior is covered even where an endpoint that
calls it (e.g. ``POST /api/auth/local/login``) can't be unit-tested end to
end because it also depends on a DB connection.
"""

import pytest
from fastapi import HTTPException, Request

from api.security import csrf_guard
from tests.conftest import TEST_ORIGIN


def _post_request(*, origin: str | None = None) -> Request:
    headers = [(b"origin", origin.encode())] if origin is not None else []
    scope = {"type": "http", "headers": headers, "method": "POST", "path": "/", "query_string": b""}
    return Request(scope)


def test_csrf_guard_allows_same_origin_post():
    csrf_guard(_post_request(origin=TEST_ORIGIN))  # must not raise


def test_csrf_guard_rejects_cross_origin_post():
    with pytest.raises(HTTPException) as exc:
        csrf_guard(_post_request(origin="https://evil.example.com"))
    assert exc.value.status_code == 403


def test_csrf_guard_rejects_missing_origin_and_referer_on_post():
    with pytest.raises(HTTPException) as exc:
        csrf_guard(_post_request())
    assert exc.value.status_code == 403
