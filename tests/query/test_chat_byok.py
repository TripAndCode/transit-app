"""Wiring test for a signed-in user's stored BYOK key into
``chat_with_tools``'s LLM-invocation path.

A signed-in caller with a stored key (``pipeline.query.user_llm_keys.
get_user_llm_key``) routes both of ``chat_with_tools``'s real LLM-invocation
sites through :func:`pipeline.query.chat._completion_with_key` — a one-off
client scoped to that caller's own provider/key — instead of the shared
:class:`~pipeline.query.llm_client.LLMClient` ladder, and skips the anon-quota
check entirely (defense-in-depth: ``anon_quota`` is never constructed for a
signed-in caller at the API layer in the first place). No test here ever
asserts on a raw key value beyond confirming it reached the one-off call —
matching this module's "never log the raw key" rule.
"""

import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.middleware.ratelimit import AnonQuotaContext
from api.range import RangeCtx
from pipeline.query import chat

DATABASE_URL = os.environ["DATABASE_URL"]


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


def _fake_user_key(provider: str = "groq", raw_key: str = "gsk_user_key", key_suffix: str = "9999"):
    return SimpleNamespace(provider=provider, raw_key=raw_key, key_suffix=key_suffix)


def _fake_text_message(text: str):
    return SimpleNamespace(content=text, tool_calls=None)


def _must_not_be_called(*_a, **_k):
    raise AssertionError("must not be called on this path")


class _BoomClient:
    """A shared client that must never be invoked on the BYOK path."""

    def chat_completions(self, **kwargs):
        _must_not_be_called()


@pytest.mark.asyncio
async def test_authenticated_user_with_byok_key_bypasses_shared_client(monkeypatch):
    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=_fake_user_key()))
    used_key = {}

    def fake_completion_with_key(provider, api_key, **kwargs):
        used_key["provider"] = provider
        used_key["api_key"] = api_key
        return _fake_text_message("ok")

    monkeypatch.setattr(chat, "_completion_with_key", fake_completion_with_key)
    # A shared-client call would fail loudly instead of the assertions below
    # quietly failing on a stale/empty used_key.
    monkeypatch.setattr(chat, "_get_client", lambda: _BoomClient())

    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=42)
    assert used_key["provider"] == "groq"
    assert used_key["api_key"] == "gsk_user_key"
    assert result["success"] is True
    assert result["answer"] == "ok"


@pytest.mark.asyncio
async def test_byok_caller_skips_anon_quota_even_when_exhausted(monkeypatch):
    """A BYOK caller must never hit AnonAskQuotaExceeded — anon_quota is never
    constructed for a signed-in caller at the API layer, but this defense-in-
    depth skip is verified directly here regardless of that upstream gate."""
    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=_fake_user_key()))
    monkeypatch.setattr(chat, "_completion_with_key", lambda *a, **k: _fake_text_message("ok"))
    monkeypatch.setattr(chat, "_get_client", lambda: _BoomClient())
    monkeypatch.setattr(chat, "check_and_consume_anon_quota", lambda *a, **k: False)

    anon_quota = AnonQuotaContext(session_key="sess", ip_key="1.2.3.4")
    result = await chat.chat_with_tools(
        "hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=42, anon_quota=anon_quota
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_non_byok_caller_uses_shared_client_and_never_looks_up_a_key(monkeypatch):
    """user_id=None (the default, matching every pre-existing caller) must
    never call get_user_llm_key and must go through the shared client exactly
    as before this feature existed."""
    lookup_calls = []

    def _record_lookup(*_a, **_k):
        lookup_calls.append(1)
        return None

    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(side_effect=_record_lookup))

    class _FakeClient:
        def chat_completions(self, **kwargs):
            return _fake_text_message("shared client answer"), None

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en")
    assert lookup_calls == []
    assert result["success"] is True
    assert result["answer"] == "shared client answer"


@pytest.mark.asyncio
async def test_signed_in_user_without_a_stored_key_uses_shared_client(monkeypatch):
    """user_id set but get_user_llm_key returns None (no key configured yet)
    must fall back to the shared client, not silently fail."""
    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=None))

    class _FakeClient:
        def chat_completions(self, **kwargs):
            return _fake_text_message("shared client answer"), None

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "_completion_with_key", _must_not_be_called)
    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=7)
    assert result["success"] is True
    assert result["answer"] == "shared client answer"


@pytest.mark.asyncio
async def test_byok_rate_limit_error_degrades_to_the_shared_rate_limited_message(monkeypatch):
    """A BYOK caller's own provider can still rate-limit them; that must
    degrade to the same honest message the shared ladder uses, not crash."""
    from openai import RateLimitError

    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=_fake_user_key()))

    def raise_rate_limit(*a, **k):
        raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)

    monkeypatch.setattr(chat, "_completion_with_key", raise_rate_limit)
    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=42)
    assert result["success"] is False
    assert result["answer"] == chat._chat_str("llm_rate_limited", "en")


@pytest.mark.asyncio
async def test_generate_proactive_insight_uses_byok_key(monkeypatch):
    """The Copilot proactive-insight path shares the same BYOK seam."""
    from pipeline.query import copilot

    monkeypatch.setattr(copilot, "get_user_llm_key", AsyncMock(return_value=_fake_user_key()))
    used_key = {}

    def fake_completion_with_key(provider, api_key, **kwargs):
        used_key["provider"] = provider
        used_key["api_key"] = api_key
        func = SimpleNamespace(name="pick_template", arguments=json.dumps({"template_id": "no_signal", "params": {}}))
        call = SimpleNamespace(function=func, id="call_1", type="function")
        return SimpleNamespace(content=None, tool_calls=[call])

    monkeypatch.setattr(copilot, "_completion_with_key", fake_completion_with_key)
    monkeypatch.setattr(copilot, "_get_client", lambda: _must_not_be_called())

    result = await copilot.generate_proactive_insight(
        "overview",
        {},
        {"headline": {"samples": 1}},
        locale="en",
        conn=object(),
        user_id=42,
    )
    assert used_key["provider"] == "groq"
    assert used_key["api_key"] == "gsk_user_key"
    assert result["text"]
