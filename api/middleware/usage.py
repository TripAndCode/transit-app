"""Counts every finished ``/api/`` request, split by whether the caller is
attributable to a user.

Separate from ``LoginRequiredMiddleware``: that gate's public surface is not
this middleware's exclusion list, and counting must keep working wherever
the gate ends up guarding less than the whole API. Placed just inside the
gate, so a 401 the gate itself answers is never counted, and so
``scope["state"]`` already carries the session user or API-key owner that
``SessionMiddleware``/``APIKeyMiddleware`` wrote earlier in the chain. A
session user or an API key's owner is counted into ``user_activity_daily``;
a signed-out caller is counted into ``anonymous_activity_daily``, which holds
route templates and no identity at all.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from api.activity import ANONYMOUS_BUFFER, BUFFER, ActivityKey, AnonymousKey, agency_id_from, today_jst
from api.middleware.login_gate import request_state

_EXEMPT_PREFIXES = ("/api/auth/",)
# Exact paths, not prefixes: sign-in plumbing only, so a signed-out
# /api/snapshots/... read (and everything else matched under /api/) is still
# counted. /api/me is exempt only as the who-am-I read; a change to the
# account through it is real use.
_EXEMPT_PATHS = frozenset({"/api/config"})
_EXEMPT_READS = frozenset({"/api/me"})
_READ_METHODS = frozenset({"GET", "HEAD"})


def _should_count(path: str, method: str) -> bool:
    if not path.startswith("/api/") or path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
        return False
    return not (path in _EXEMPT_READS and method in _READ_METHODS)


class UsageCountMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = {"code": 0}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _record(scope, status["code"])


def _record(scope: Scope, status_code: int) -> None:
    """Count one finished request. Skipped for sign-in plumbing, for requests
    no API route matched (the SPA fallback answers unknown /api paths) or
    whose method the matched route doesn't serve, and for anything outside
    /api/."""
    if not _should_count(scope["path"], scope["method"]):
        return
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template is None or getattr(route, "name", None) == "spa_fallback":
        return
    # A method the route doesn't serve still matches it (and is answered 405);
    # counting it would make the caller's method string part of the key.
    methods = getattr(route, "methods", None)
    if methods is not None and scope["method"] not in methods:
        return
    day = today_jst()
    method = scope["method"]
    agency_id = agency_id_from(scope.get("path_params"))
    # status 0: the app raised before starting a response.
    is_error = status_code >= 400 or status_code == 0
    state = request_state(scope)
    user = state.get("user")
    if user is not None:
        BUFFER.record(ActivityKey(user.user_id, day, template, method, agency_id, False), is_error=is_error)
    elif state.get("api_key_owner_id") is not None:
        BUFFER.record(ActivityKey(state["api_key_owner_id"], day, template, method, agency_id, True), is_error=is_error)
    elif state.get("api_key_authenticated"):
        # A key with no owner row: credentialed, so not a reader, and no
        # account to count it against.
        return
    else:
        # A signed-out caller can name any agency id in the path, and every
        # distinct one would add a permanent row. Every agency-scoped route a
        # signed-out caller can reach answers an unknown agency with an error,
        # so only a request that succeeded keeps its agency.
        ANONYMOUS_BUFFER.record(
            AnonymousKey(day, template, method, agency_id if not is_error else None), is_error=is_error
        )
