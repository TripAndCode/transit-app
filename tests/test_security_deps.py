import pytest
from fastapi import HTTPException, Request

from api.security import User, current_user, require_admin, require_user


def _request_with_state(user):
    scope = {"type": "http", "headers": [], "method": "GET", "path": "/", "query_string": b""}
    req = Request(scope)
    req.state.user = user
    return req


def test_require_user_anonymous_raises():
    req = _request_with_state(None)
    with pytest.raises(HTTPException) as exc:
        require_user(req)
    assert exc.value.status_code == 401


def test_require_user_returns_user():
    u = User(user_id=1, email="a@x", name=None, avatar_url=None, role="user", suspended_at=None)
    req = _request_with_state(u)
    assert require_user(req) is u


def test_require_admin_non_admin_raises():
    u = User(user_id=1, email="a@x", name=None, avatar_url=None, role="user", suspended_at=None)
    req = _request_with_state(u)
    with pytest.raises(HTTPException) as exc:
        require_admin(req)
    assert exc.value.status_code == 403


def test_require_admin_admin_returns():
    u = User(user_id=1, email="a@x", name=None, avatar_url=None, role="admin", suspended_at=None)
    req = _request_with_state(u)
    assert require_admin(req) is u


def test_current_user_returns_none_when_anonymous():
    req = _request_with_state(None)
    assert current_user(req) is None
