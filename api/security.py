"""Auth dependencies + User dataclass.

`require_user` / `require_admin` are FastAPI dependencies that read the
user previously loaded by `session_middleware` into `request.state.user`.
Keep this module dependency-free of asyncpg so it stays cheap to import
in unit tests.
"""

import os as _os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

_PUBLIC_BASE_URL = _os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
_ALLOW_TEST_ORIGIN = _os.environ.get("ALLOW_TEST_ORIGIN") == "1"
_CORS_ORIGINS = tuple(
    o.strip() for o in _os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
)


@dataclass(frozen=True)
class User:
    """Authenticated user materialized from the sessions table."""

    user_id: int
    email: str
    name: str | None
    avatar_url: str | None
    role: str
    suspended_at: datetime | None


def current_user(request: Request) -> User | None:
    """Return the user attached to request state by session middleware, or None."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    """FastAPI dependency that 401s when no authenticated user is present."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="auth required")
    return user


def require_admin(request: Request) -> User:
    """FastAPI dependency that 403s unless the caller has the ``admin`` role."""
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _serialized_origin(value: str) -> str | None:
    """Reduce an Origin or Referer header value to its serialized origin
    form (``scheme://host[:port]``, lowercased), or None if it can't be
    parsed. Strips path / query / fragment so a Referer with a path is
    normalised against an Origin, and a hostile path-suffixed Origin
    like ``http://localhost:8000/.evil.com`` no longer prefix-matches.
    """
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def csrf_guard(request: Request) -> None:
    """Reject mutating requests whose Origin/Referer is cross-site.

    Combined with SameSite=Lax cookies, this defeats CSRF without a
    separate token. Same-origin SPA POSTs always send Origin matching
    PUBLIC_BASE_URL.

    Both incoming and allowed values are reduced to a serialized origin
    (scheme://host[:port], lowercased) before equality compare, so
    path-suffixed bypasses like ``Origin: http://localhost:8000/.evil``
    are rejected.

    The ``http://test`` ASGITransport origin is only allowed when
    ``ALLOW_TEST_ORIGIN=1`` so production deployments don't accidentally
    accept that base from non-browser callers.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    raw = request.headers.get("origin") or request.headers.get("referer", "")
    incoming = _serialized_origin(raw)
    if incoming is None:
        raise HTTPException(status_code=403, detail="origin required")
    allowed: set[str] = set()
    base_norm = _serialized_origin(_PUBLIC_BASE_URL)
    if base_norm is not None:
        allowed.add(base_norm)
    for o in _CORS_ORIGINS:
        n = _serialized_origin(o)
        if n is not None:
            allowed.add(n)
    if _ALLOW_TEST_ORIGIN:
        allowed.add("http://test")
    if incoming in allowed:
        return
    raise HTTPException(status_code=403, detail="cross-origin request denied")
