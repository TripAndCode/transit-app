"""Sign-in gate for the API.

Deny by default: every ``/api/*`` path and the OpenAPI docs need a signed-in
caller or a valid API key, except the short allow-list the sign-in flow
itself and the pre-sign-in SPA need. A new endpoint is gated without anyone
having to remember to gate it; ``tests/unit/test_login_gate_surface.py``
pins which routes are public.

Enforced only while SSO is configured and the ``login_required`` flag is
on. With SSO unset (anonymous-only mode, the local-dev default) nobody could
sign in, so the gate stays open.

Pure ASGI rather than ``BaseHTTPMiddleware``: it runs inside
``SessionMiddleware`` and ``APIKeyMiddleware``, which write
``request.state`` into ``scope["state"]``, so it reads the caller from
there.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.sso import sso_status
from pipeline.flags import aflag

AUTH_REQUIRED = {"detail": "auth required"}

_PUBLIC_PREFIXES = ("/api/auth/",)
_PUBLIC_PATHS = frozenset({"/api/config"})
_GATED_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


def needs_login(path: str) -> bool:
    """Whether ``path`` is refused to a signed-out caller while the gate is enforced."""
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return False
    return path in _GATED_PATHS or path.startswith("/api/")


async def enforcement_active() -> bool:
    """Whether the API refuses signed-out callers right now."""
    enabled, _ = sso_status()
    return enabled and await aflag("login_required")


def request_state(scope: Scope) -> dict:
    state = scope.get("state")
    return state if isinstance(state, dict) else {}


class LoginRequiredMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not needs_login(scope["path"]):
            await self.app(scope, receive, send)
            return
        state = request_state(scope)
        signed_in = state.get("user") is not None or bool(state.get("api_key_authenticated"))
        if not signed_in and await enforcement_active():
            await JSONResponse(AUTH_REQUIRED, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
