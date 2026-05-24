"""ASGI middleware that assigns each request an ID, times it, and emits
one access-log line.

Sits OUTERMOST on the request side (added LAST in api/main.py's
middleware list so Starlette wraps it last -> it enters first) so it
sees the final response status after every inner middleware ran.

The request ID is either:
- the client's `X-Request-Id` header value, if it matches
  `^[A-Za-z0-9\\-]{1,64}$` (RFC-ish: ASCII alnum + dash, <=64 chars), or
- a fresh `uuid4().hex`.

The validation rejects newlines, control chars, and oversize values so
log lines stay one-per-line and the response header stays sane for
downstream consumers (Caddy, browsers).
"""

import logging
import re
import time
import uuid
from typing import Awaitable, Callable

from api.logging_config import REQUEST_ID_CTX

_log = logging.getLogger("api.access")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-]{1,64}$")


def _resolve_request_id(headers: list[tuple[bytes, bytes]]) -> str:
    """Return a sanitised request ID. Uses the client's X-Request-Id
    when well-formed; otherwise generates a fresh UUID4 hex."""
    for k, v in headers:
        if k == b"x-request-id":
            try:
                candidate = v.decode("ascii")
            except UnicodeDecodeError:
                break
            if _REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
            break
    return uuid.uuid4().hex


def _safe_user_id(scope) -> str:
    """Read request.state.user.user_id without importing FastAPI types.
    Returns '-' on any missing attribute so the format string is always
    resolvable."""
    state = scope.get("state")
    if not state:
        return "-"
    user = getattr(state, "user", None)
    if user is None:
        return "-"
    uid = getattr(user, "user_id", None)
    return str(uid) if uid is not None else "-"


class RequestLogMiddleware:
    """ASGI middleware: assign request_id, time the request, log one
    access line. See module docstring for placement."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope.get("headers", []))
        token = REQUEST_ID_CTX.set(request_id)
        start = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            user_id = _safe_user_id(scope)
            path = scope.get("path", "?")
            method = scope.get("method", "?")
            _log.info(
                "method=%s path=%s status=%d duration_ms=%d user_id=%s",
                method,
                path,
                status_holder["status"],
                duration_ms,
                user_id,
            )
            REQUEST_ID_CTX.reset(token)
