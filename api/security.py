"""Auth dependencies + CSRF guard.

`require_user` / `require_admin` are FastAPI dependencies that read the
user previously loaded by `SessionMiddleware` into `request.state.user`.
`csrf_guard` Origin/Referer-gates mutating routes via a serialized-origin
equality against the env-driven allow-list. Keep this module
dependency-free of asyncpg so it stays cheap to import in unit tests.
"""

import hashlib
import hmac
import os as _os
import secrets
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

_PUBLIC_BASE_URL = _os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
_ALLOW_TEST_ORIGIN = _os.environ.get("ALLOW_TEST_ORIGIN") == "1"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    """Hash ``password`` with scrypt (stdlib, no new dependency) for the one
    local/break-glass admin account. Format: ``scrypt$<salt-hex>$<hash-hex>``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check against a hash produced by ``hash_password``.
    Returns False (never raises) for malformed/missing hashes."""
    if not stored:
        return False
    try:
        algo, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return hmac.compare_digest(dk.hex(), hash_hex)


def cookie_secure() -> bool:
    """True when cookies should set ``Secure`` — i.e. the deployment is served
    over HTTPS. Read live from the env (not the import-frozen ``_PUBLIC_BASE_URL``)
    so a per-process config flip is honored. Local-dev over ``http://localhost``
    returns False so the browser still sends the cookie and SSO works.
    """
    return _os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").startswith("https://")


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
    parsed. A Referer with a path normalises down to its origin, but a
    bare Origin header per RFC 6454 must itself be path-less — a
    request claiming ``Origin: http://localhost:8000/.evil`` is rejected
    rather than silently collapsing to the trusted base.
    """
    if not value or value != value.strip():
        # HTTP transports already strip Origin's OWS, so this is
        # defence-in-depth against a future caller constructing a Request
        # in-process with a padded header value. Python's urlsplit
        # silently absorbs a leading space, which would otherwise let
        # such a caller smuggle a trusted origin past the allow-list.
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _has_origin_path(value: str) -> bool:
    """True if `value` carries a non-trivial path (anything beyond '' or '/').

    Origin headers are path-less by RFC 6454; Referer headers carry a path.
    `csrf_guard` uses this to apply the stricter no-path rule only to Origin.
    """
    return urlsplit(value).path not in ("", "/")


def _build_allowed_origins() -> frozenset[str]:
    """Compute the allow-list once at import; csrf_guard reads it per request."""
    out: set[str] = set()
    base_norm = _serialized_origin(_PUBLIC_BASE_URL)
    if base_norm is not None:
        out.add(base_norm)
    for o in _CORS_ORIGINS:
        n = _serialized_origin(o)
        if n is not None:
            out.add(n)
    if _ALLOW_TEST_ORIGIN:
        out.add("http://test")
    return frozenset(out)


# Frozen at import time — tests that monkeypatch `PUBLIC_BASE_URL` /
# `CORS_ORIGINS` / `ALLOW_TEST_ORIGIN` after this point will not see the
# change. Reload the module or call `_build_allowed_origins()` and
# reassign in a fixture if you need a different allow-list per test.
_ALLOWED_ORIGINS = _build_allowed_origins()


def csrf_guard(request: Request) -> None:
    """Reject mutating requests whose Origin/Referer is cross-site.

    Combined with SameSite=Lax cookies, this defeats CSRF without a
    separate token. Same-origin SPA POSTs always send Origin matching
    PUBLIC_BASE_URL.

    The Origin header (if present) must be path-less per RFC 6454;
    ``http://localhost:8000/.evil`` is rejected outright rather than
    collapsing to the trusted base. Referer falls back when Origin is
    absent and is normalised to its serialized origin.

    The ``http://test`` ASGITransport origin is only allowed when
    ``ALLOW_TEST_ORIGIN=1`` so production deployments don't accidentally
    accept that base from non-browser callers.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin_raw = request.headers.get("origin")
    if origin_raw is not None and _has_origin_path(origin_raw):
        raise HTTPException(status_code=403, detail="cross-origin request denied")
    raw = origin_raw or request.headers.get("referer") or ""
    incoming = _serialized_origin(raw)
    if incoming is None:
        raise HTTPException(status_code=403, detail="origin required")
    if incoming in _ALLOWED_ORIGINS:
        return
    raise HTTPException(status_code=403, detail="cross-origin request denied")
