"""Request-shape bounds on inputs that were typed as `Any`/`str` or not bounded
at all. Pure model validation, plus one standalone app checking a CORS
setting; no database."""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from api.routers.auth import ProviderRejected, _fetch_userinfo
from api.routers.conversations import _MAX_FILTER_CTX_BYTES, CreateConversation, UpdateConversation
from tests.fixtures.routes import api_routes


# ── CORS ─────────────────────────────────────────────────────────────────────
def test_cors_allows_every_method_a_mounted_route_uses():
    import api.main

    cors = next(m for m in api.main.app.user_middleware if m.cls is CORSMiddleware)
    used_methods: set[str] = set()
    for route in api_routes(api.main.app.routes):
        used_methods |= route.methods - {"HEAD", "OPTIONS"}
    assert used_methods <= set(cors.kwargs["allow_methods"])


# ── conversations filter_ctx ─────────────────────────────────────────────────
def _oversized_ctx() -> dict:
    return {"routes": ["r" * 64] * (_MAX_FILTER_CTX_BYTES // 60)}


def test_create_conversation_bounds_filter_ctx():
    CreateConversation(title="t", filter_ctx={"from": "2026-01-01"})
    with pytest.raises(ValidationError):
        CreateConversation(title="t", filter_ctx=_oversized_ctx())


def test_update_conversation_bounds_filter_ctx_but_allows_none():
    assert UpdateConversation(filter_ctx=None).filter_ctx is None
    with pytest.raises(ValidationError):
        UpdateConversation(filter_ctx=_oversized_ctx())


# ── GitHub error bodies ──────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _GitHub:
    def __init__(self, user, emails):
        self._user, self._emails = user, emails

    async def get(self, url, token):
        return _Resp(self._user if url == "user" else self._emails)


async def test_a_github_error_body_is_a_rejection_not_a_keyerror():
    bad = {"message": "Bad credentials", "documentation_url": "https://docs.github.com"}
    with pytest.raises(ProviderRejected):
        await _fetch_userinfo(_GitHub(bad, bad), {"access_token": "x"}, "github")


async def test_a_well_formed_github_body_still_resolves():
    info = await _fetch_userinfo(
        _GitHub(
            {"id": 7, "login": "octo", "avatar_url": None}, [{"email": "o@x.io", "primary": True, "verified": True}]
        ),
        {"access_token": "x"},
        "github",
    )
    assert info == {"sub": "7", "email": "o@x.io", "email_verified": True, "name": "octo", "avatar_url": None}
