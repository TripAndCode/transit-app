"""Cookie → DB lookup → request.state.user.

Mounted before APIKeyMiddleware in api/main.py so per-request handlers
see the user (or None) on request.state. last_seen_at writes are
throttled in-process to 1/min/sid.
"""

import os
import time
from datetime import datetime, timezone

import asyncpg
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from api.security import User

SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "sid")

# tiny in-process throttle: sid -> last touch time (epoch seconds)
_TOUCH_THROTTLE: dict[str, float] = {}
_TOUCH_INTERVAL_SEC = 60.0
_TOUCH_MAX = 10_000  # bound memory


def _should_touch(sid: str) -> bool:
    """Return True at most once per ``_TOUCH_INTERVAL_SEC`` per sid."""
    now = time.monotonic()
    last = _TOUCH_THROTTLE.get(sid, 0.0)
    if now - last < _TOUCH_INTERVAL_SEC:
        return False
    if len(_TOUCH_THROTTLE) >= _TOUCH_MAX:
        # cheap eviction: drop everything when full
        _TOUCH_THROTTLE.clear()
    _TOUCH_THROTTLE[sid] = now
    return True


class SessionMiddleware(BaseHTTPMiddleware):
    """Loads the session cookie into ``request.state.user`` for downstream deps."""

    async def dispatch(self, request: Request, call_next):
        """Resolve the session cookie to a User, or pass through for static/health paths."""
        request.state.user = None
        path = request.url.path
        if path.startswith("/assets/") or path in ("/health", "/favicon.ico"):
            return await call_next(request)
        sid = request.cookies.get(SESSION_COOKIE_NAME)
        clear_cookie = False
        if sid:
            pool: asyncpg.Pool = request.app.state.pool
            row = await pool.fetchrow(
                """
                SELECT s.sid, s.expires_at,
                       u.user_id, u.email, u.name, u.avatar_url, u.role, u.suspended_at
                FROM sessions s
                JOIN users u USING (user_id)
                WHERE s.sid = $1
                """,
                sid,
            )
            now = datetime.now(timezone.utc)
            if row and row["expires_at"] > now and row["suspended_at"] is None:
                request.state.user = User(
                    user_id=row["user_id"],
                    email=row["email"],
                    name=row["name"],
                    avatar_url=row["avatar_url"],
                    role=row["role"],
                    suspended_at=row["suspended_at"],
                )
                if _should_touch(sid):
                    await pool.execute("UPDATE sessions SET last_seen_at = now() WHERE sid = $1", sid)
            elif row:
                # expired or suspended → delete server-side, clear client
                await pool.execute("DELETE FROM sessions WHERE sid = $1", sid)
                clear_cookie = True
            else:
                clear_cookie = True

        response = await call_next(request)
        if clear_cookie:
            response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return response
