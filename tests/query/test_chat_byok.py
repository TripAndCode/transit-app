"""Wiring test for a signed-in user's stored BYOK key into
``chat_with_tools``'s LLM-invocation path.

A signed-in caller with a stored key (``pipeline.query.user_llm_keys.
get_user_llm_key``) routes both of ``chat_with_tools``'s real LLM-invocation
sites through :func:`pipeline.query.chat._completion_with_key` — a one-off
client scoped to that caller's own provider/key — instead of the shared
:class:`~pipeline.query.llm_client.LLMClient` ladder. No test here ever
asserts on a raw key value beyond confirming it reached the one-off call —
matching this module's "never log the raw key" rule.
"""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.range import RangeCtx
from pipeline.query import chat


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


def _fake_user_key(provider: str = "gemini", raw_key: str = "gemini_user_key", key_suffix: str = "9999"):
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
    assert used_key["provider"] == "gemini"
    assert used_key["api_key"] == "gemini_user_key"
    assert result["success"] is True
    assert result["answer"] == "ok"


@pytest.mark.asyncio
async def test_byok_provider_disallowed_by_operator_degrades_without_calling(monkeypatch):
    """ASK_CHAT_ALLOWED_PROVIDERS must gate a BYOK caller's stored provider
    too, not just the shared ladder -- an operator who restricts the ladder
    to exclude an injection-prone provider must not have that policy
    silently bypassed just because the caller supplied their own key of the
    excluded provider."""
    monkeypatch.setenv("ASK_CHAT_ALLOWED_PROVIDERS", "openai")
    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=_fake_user_key(provider="gemini")))
    monkeypatch.setattr(chat, "_completion_with_key", _must_not_be_called)
    monkeypatch.setattr(chat, "_get_client", lambda: _BoomClient())

    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=42)
    assert result["success"] is False
    assert result["answer"] == chat._chat_str("llm_unconfigured", "en")


@pytest.mark.asyncio
async def test_byok_provider_allowed_by_operator_allowlist_proceeds(monkeypatch):
    """A BYOK provider that IS in the operator's allowlist still works."""
    monkeypatch.setenv("ASK_CHAT_ALLOWED_PROVIDERS", "gemini, openai")
    monkeypatch.setattr(chat, "get_user_llm_key", AsyncMock(return_value=_fake_user_key(provider="gemini")))
    monkeypatch.setattr(chat, "_completion_with_key", lambda *a, **k: _fake_text_message("ok"))
    monkeypatch.setattr(chat, "_get_client", lambda: _BoomClient())

    result = await chat.chat_with_tools("hi", _ctx(), conn=None, agency_id=1, locale="en", user_id=42)
    assert result["success"] is True
    assert result["answer"] == "ok"


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
    """The Copilot proactive-insight path shares the same BYOK seam.

    ``generate_proactive_insight`` takes an already-resolved ``user_key``
    rather than a ``conn``/``user_id`` pair: the caller (the API router)
    resolves the key with its own short-lived pooled connection, released
    before this (multi-second) LLM call — never held across it.
    """
    from pipeline.query import copilot

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
        user_key=_fake_user_key(),
    )
    assert used_key["provider"] == "gemini"
    assert used_key["api_key"] == "gemini_user_key"
    assert result["text"]


def test_completion_with_key_omits_tool_choice_when_no_tools():
    """Same OpenAI-compat fix as LLMClient.chat_completions, on the BYOK
    one-off path: no tools -> tools/tool_choice both absent, not
    tools=None+tool_choice="none" (OpenAI rejects tool_choice without tools)."""
    from unittest.mock import patch

    fake_response = MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = fake_response
        chat._completion_with_key("gemini", "gemini_user_key", messages=[{"role": "user", "content": "hi"}])
    _, create_kwargs = mock_openai.return_value.chat.completions.create.call_args
    assert "tools" not in create_kwargs
    assert "tool_choice" not in create_kwargs
