"""LLMClient provider-fallback unit tests."""

from unittest.mock import MagicMock, patch

import httpx2
import pytest

from pipeline.query import llm_client


@pytest.fixture(autouse=True)
def _reset():
    llm_client.reset_client_for_tests()
    yield
    llm_client.reset_client_for_tests()


def _set_providers(monkeypatch, providers, **keys):
    monkeypatch.setenv("CHAT_PROVIDERS", providers)
    for k, v in keys.items():
        monkeypatch.setenv(k, v)
    # Clear any env we don't want to leak
    for unset in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        if unset not in keys:
            monkeypatch.delenv(unset, raising=False)


def test_no_providers_returns_none(monkeypatch):
    _set_providers(monkeypatch, providers="")
    client = llm_client.LLMClient()
    assert client.providers() == []
    msg, kind = client.chat_completions(messages=[{"role": "user", "content": "hi"}])
    assert msg is None
    assert kind == "no_providers"


def test_missing_api_key_provider_skipped(monkeypatch):
    """Provider listed but no API key set → drop from ladder."""
    _set_providers(monkeypatch, providers="gemini,openai", OPENAI_API_KEY="real-openai")
    client = llm_client.LLMClient()
    names = [p.name for p in client.providers()]
    assert names == ["openai"]  # gemini dropped


def test_first_provider_success(monkeypatch):
    """Gemini returns a message → openai never called."""
    _set_providers(monkeypatch, providers="gemini,openai", GEMINI_API_KEY="c", OPENAI_API_KEY="o")
    fake_message = MagicMock(name="message", content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    with patch("openai.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = fake_response
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is fake_message
    assert kind is None
    assert mock_openai.call_count == 1
    _args, kwargs = mock_openai.call_args
    assert kwargs.get("base_url") == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_no_tools_omits_tool_choice_key(monkeypatch):
    """No tools → tools/tool_choice are absent, not tools=None+tool_choice="none".

    OpenAI rejects tool_choice outright when tools isn't specified ("tool_choice
    is only allowed when tools are specified") -- this is the JSON-mode
    (ASK_INTENT_CACHE_ENABLED) request shape, which never passes tools."""
    _set_providers(monkeypatch, providers="gemini", GEMINI_API_KEY="c")
    fake_response = MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = fake_response
        llm_client.LLMClient().chat_completions(messages=[], response_format={"type": "json_object"})
    _, create_kwargs = mock_openai.return_value.chat.completions.create.call_args
    assert "tools" not in create_kwargs
    assert "tool_choice" not in create_kwargs


def test_first_provider_rate_limited_falls_back(monkeypatch):
    """Gemini 429 → openai used."""
    from openai import RateLimitError

    _set_providers(monkeypatch, providers="gemini,openai", GEMINI_API_KEY="c", OPENAI_API_KEY="o")
    fake_message = MagicMock(content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    call_count = {"n": 0}

    def fake_create(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)
        return fake_response

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = fake_create
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is fake_message
    assert kind is None
    assert call_count["n"] == 2  # gemini failed, openai succeeded


def test_all_providers_rate_limited_returns_none(monkeypatch):
    from openai import RateLimitError

    _set_providers(monkeypatch, providers="gemini,openai", GEMINI_API_KEY="c", OPENAI_API_KEY="o")

    def always_429(*a, **kw):
        raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_429
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "rate_limit"


def test_gemini_requires_api_key(monkeypatch):
    """Gemini listed but no API key set → dropped from the ladder like any other provider."""
    _set_providers(monkeypatch, providers="gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = llm_client.LLMClient()
    assert client.providers() == []


def test_gemini_provider_defaults(monkeypatch):
    """Gemini resolves its documented default base_url/model when unset."""
    _set_providers(monkeypatch, providers="gemini", GEMINI_API_KEY="g")
    client = llm_client.LLMClient()
    cfg = client.providers()[0]
    assert cfg.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert cfg.model == "gemini-3.1-flash-lite"


def test_openai_requires_api_key(monkeypatch):
    """OpenAI listed but no API key set → dropped from the ladder."""
    _set_providers(monkeypatch, providers="openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = llm_client.LLMClient()
    assert client.providers() == []


def test_openai_provider_defaults(monkeypatch):
    """OpenAI resolves its documented default base_url/model when unset."""
    _set_providers(monkeypatch, providers="openai", OPENAI_API_KEY="o")
    client = llm_client.LLMClient()
    cfg = client.providers()[0]
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-5.4-mini"


def test_per_provider_base_url_override(monkeypatch):
    """Operator can override base_url via env."""
    _set_providers(monkeypatch, providers="gemini", GEMINI_API_KEY="c", GEMINI_BASE_URL="https://example.test/v1")
    client = llm_client.LLMClient()
    assert client.providers()[0].base_url == "https://example.test/v1"


def test_retry_once_on_transient_then_success(monkeypatch):
    from types import SimpleNamespace

    from openai import APIConnectionError

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    llm_client.reset_client_for_tests()

    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIConnectionError(request=httpx2.Request("POST", "http://test"))
        return ok

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = flaky
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is not None and msg.content == "ok"
    assert kind is None
    assert calls["n"] == 2


def test_last_error_kind_connection_exhausted(monkeypatch):
    """Transient error that never clears: retried once, then (None, 'connection')."""
    from openai import APIConnectionError

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def always_down(*a, **k):
        calls["n"] += 1
        raise APIConnectionError(request=httpx2.Request("POST", "http://test"))

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_down
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "connection"
    assert calls["n"] == 2  # single provider, retried exactly once


def test_last_error_kind_rate_limit(monkeypatch):
    from openai import RateLimitError

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    llm_client.reset_client_for_tests()

    def always_429(*a, **k):
        raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_429
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "rate_limit"


def test_last_error_kind_no_providers(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDERS", "")
    llm_client.reset_client_for_tests()
    msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "no_providers"


def test_timeout_does_not_retry(monkeypatch):
    """APITimeoutError descends immediately (no retry — it already waited the deadline)."""
    from openai import APITimeoutError

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def always_timeout(*a, **k):
        calls["n"] += 1
        raise APITimeoutError(request=httpx2.Request("POST", "http://test"))

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_timeout
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "connection"
    assert calls["n"] == 1  # NOT retried


def test_openai_client_disables_sdk_retries(monkeypatch):
    """We own retry/fallback; the SDK's internal retry must be off — else a 429
    blocks ~60s before our ladder descent fires, making the fallback illusory."""
    _set_providers(monkeypatch, providers="gemini", GEMINI_API_KEY="c")
    fake_response = MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = fake_response
        llm_client.LLMClient().chat_completions(messages=[])
    _, kwargs = mock_openai.call_args
    assert kwargs.get("max_retries") == 0


def test_rate_limit_preferred_over_later_connection(monkeypatch):
    """Earlier provider 429 + later provider connection-fail → surfaced kind is rate_limit."""
    from openai import APIConnectionError, RateLimitError

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini,openai")
    monkeypatch.setenv("GEMINI_API_KEY", "c")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def side(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # gemini → 429
            raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)
        # openai → connection (retried then descends)
        raise APIConnectionError(request=httpx2.Request("POST", "http://test"))

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = side
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "rate_limit"


def test_allowed_providers_filters_ladder(monkeypatch):
    """allowed_providers restricts the ladder — an earlier but disallowed
    provider (openai) is skipped in favour of the allowed one (gemini)."""
    _set_providers(monkeypatch, providers="openai,gemini", GEMINI_API_KEY="c", OPENAI_API_KEY="o")
    fake_message = MagicMock(content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = fake_response
        msg, kind = llm_client.LLMClient().chat_completions(messages=[], allowed_providers={"gemini"})
    assert msg is fake_message and kind is None
    assert mock_openai.call_count == 1  # openai never constructed
    _args, kwargs = mock_openai.call_args
    assert kwargs.get("base_url") == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_allowed_providers_empty_intersection_degrades(monkeypatch):
    """When no configured provider is allowed, return no_providers without any
    network call — the caller degrades rather than using a disallowed one."""
    _set_providers(monkeypatch, providers="openai", OPENAI_API_KEY="o")
    with patch("openai.OpenAI") as mock_openai:
        msg, kind = llm_client.LLMClient().chat_completions(messages=[], allowed_providers={"gemini"})
    assert msg is None and kind == "no_providers"
    assert mock_openai.call_count == 0  # no provider was attempted


def test_followup_allowed_providers_defaults_to_gemini_and_openai(monkeypatch):
    """The follow-up restricts itself to the injection-resistant providers verified
    via scripts/followup_eval.py."""
    from pipeline.query import followup

    monkeypatch.delenv("ASK_FOLLOWUP_PROVIDERS", raising=False)
    assert followup._allowed_providers() == {"gemini", "openai"}
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", "gemini")
    assert followup._allowed_providers() == {"gemini"}
