"""LLMClient provider-fallback unit tests."""

import json
from unittest.mock import MagicMock, patch

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
    for unset in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OLLAMA_API_KEY"):
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
    _set_providers(monkeypatch, providers="cerebras,groq", GROQ_API_KEY="real-groq")
    client = llm_client.LLMClient()
    names = [p.name for p in client.providers()]
    assert names == ["groq"]  # cerebras dropped


def test_first_provider_success(monkeypatch):
    """Cerebras returns a message → groq never called."""
    _set_providers(monkeypatch, providers="cerebras,groq", CEREBRAS_API_KEY="c", GROQ_API_KEY="g")
    fake_message = MagicMock(name="message", content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    with patch("openai.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = fake_response
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is fake_message
    assert kind is None
    assert mock_openai.call_count == 1
    args, kwargs = mock_openai.call_args
    assert kwargs.get("base_url") == "https://api.cerebras.ai/v1"


def test_first_provider_rate_limited_falls_back(monkeypatch):
    """Cerebras 429 → groq used."""
    from openai import RateLimitError

    _set_providers(monkeypatch, providers="cerebras,groq", CEREBRAS_API_KEY="c", GROQ_API_KEY="g")
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
    assert call_count["n"] == 2  # cerebras failed, groq succeeded


def test_all_providers_rate_limited_returns_none(monkeypatch):
    from openai import RateLimitError

    _set_providers(monkeypatch, providers="cerebras,groq", CEREBRAS_API_KEY="c", GROQ_API_KEY="g")

    def always_429(*a, **kw):
        raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_429
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "rate_limit"


def test_ollama_does_not_require_api_key(monkeypatch):
    """Ollama provider should appear in the ladder even with no API key set."""
    _set_providers(monkeypatch, providers="ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    client = llm_client.LLMClient()
    names = [p.name for p in client.providers()]
    assert names == ["ollama"]


def test_per_provider_base_url_override(monkeypatch):
    """Operator can override base_url via env."""
    _set_providers(monkeypatch, providers="cerebras", CEREBRAS_API_KEY="c", CEREBRAS_BASE_URL="https://example.test/v1")
    client = llm_client.LLMClient()
    assert client.providers()[0].base_url == "https://example.test/v1"


def test_recover_tool_call_valid():
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = {
            "error": {
                "code": "tool_use_failed",
                "failed_generation": '<function=top_n({"metric": "avg_delay", "n": 10})</function>',
            }
        }

    msg = _recover_tool_call(_Exc())
    assert msg is not None
    assert msg.tool_calls[0].function.name == "top_n"
    assert json.loads(msg.tool_calls[0].function.arguments) == {"metric": "avg_delay", "n": 10}
    assert msg.content is None


def test_recover_tool_call_not_tool_use_failed():
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = {"error": {"code": "context_length_exceeded", "message": "too long"}}

    assert _recover_tool_call(_Exc()) is None


def test_recover_tool_call_malformed_generation():
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = {"error": {"code": "tool_use_failed", "failed_generation": "garbage no function tag"}}

    assert _recover_tool_call(_Exc()) is None


def test_recover_tool_call_non_json_args():
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = {"error": {"code": "tool_use_failed", "failed_generation": "<function=top_n(not json)</function>"}}

    assert _recover_tool_call(_Exc()) is None


def test_recover_tool_call_no_body():
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = None

    assert _recover_tool_call(_Exc()) is None


def test_recovery_short_circuits_failover(monkeypatch):
    """A tool_use_failed 400 on the first provider is recovered, not failed-over."""
    from openai import BadRequestError

    monkeypatch.setenv("CHAT_PROVIDERS", "cerebras,groq")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    llm_client.reset_client_for_tests()

    # A real BadRequestError subclass so the type-filtered ``except`` in
    # chat_completions catches it; bypass the SDK __init__ (needs an httpx
    # response) and inject only the ``body`` _recover_tool_call reads.
    class _FakeBadRequest(BadRequestError):
        def __init__(self):
            self.body = {
                "error": {
                    "code": "tool_use_failed",
                    "failed_generation": '<function=top_n({"n":5})</function>',
                }
            }

    def raise_tool_use_failed(*a, **k):
        raise _FakeBadRequest()

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = raise_tool_use_failed
        msg, kind = llm_client.LLMClient().chat_completions(messages=[], tools=[{"x": 1}])
    assert msg is not None
    assert kind is None
    assert msg.tool_calls[0].function.name == "top_n"
    assert mock_openai.return_value.chat.completions.create.call_count == 1


def test_retry_once_on_transient_then_success(monkeypatch):
    from types import SimpleNamespace

    from openai import APIConnectionError

    monkeypatch.setenv("CHAT_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    llm_client.reset_client_for_tests()

    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIConnectionError(request=None)
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

    monkeypatch.setenv("CHAT_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def always_down(*a, **k):
        calls["n"] += 1
        raise APIConnectionError(request=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_down
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "connection"
    assert calls["n"] == 2  # single provider, retried exactly once


def test_last_error_kind_rate_limit(monkeypatch):
    from openai import RateLimitError

    monkeypatch.setenv("CHAT_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g")
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


def test_cerebras_default_model_is_gpt_oss(monkeypatch):
    """No CEREBRAS_MODEL override → the account-available gpt-oss-120b."""
    monkeypatch.delenv("CEREBRAS_MODEL", raising=False)
    _set_providers(monkeypatch, providers="cerebras", CEREBRAS_API_KEY="c")
    providers = llm_client.LLMClient().providers()
    assert providers[0].name == "cerebras"
    assert providers[0].model == "gpt-oss-120b"


@pytest.mark.parametrize(
    "generation",
    [
        '<function=top_n({"n": 5})</function>',  # paren form
        '<function=top_n>{"n": 5}</function>',  # tag form, no parens
        '<function=top_n{"n": 5}>',  # bare braces
    ],
)
def test_recover_tool_call_format_variants(generation):
    """Recovery must handle the paren / tag / bare shapes llama emits."""
    from pipeline.query.llm_client import _recover_tool_call

    class _Exc:
        body = {"error": {"code": "tool_use_failed", "failed_generation": generation}}

    msg = _recover_tool_call(_Exc())
    assert msg is not None
    assert msg.tool_calls[0].function.name == "top_n"
    assert json.loads(msg.tool_calls[0].function.arguments) == {"n": 5}


def test_timeout_does_not_retry(monkeypatch):
    """APITimeoutError descends immediately (no retry — it already waited the deadline)."""
    from openai import APITimeoutError

    monkeypatch.setenv("CHAT_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def always_timeout(*a, **k):
        calls["n"] += 1
        raise APITimeoutError(request=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_timeout
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "connection"
    assert calls["n"] == 1  # NOT retried


def test_openai_client_disables_sdk_retries(monkeypatch):
    """We own retry/fallback; the SDK's internal retry must be off — else a 429
    blocks ~60s before our ladder descent fires, making the fallback illusory."""
    _set_providers(monkeypatch, providers="cerebras", CEREBRAS_API_KEY="c")
    fake_response = MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))])
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = fake_response
        llm_client.LLMClient().chat_completions(messages=[])
    _, kwargs = mock_openai.call_args
    assert kwargs.get("max_retries") == 0


def test_rate_limit_preferred_over_later_connection(monkeypatch):
    """Earlier provider 429 + later provider connection-fail → surfaced kind is rate_limit."""
    from openai import APIConnectionError, RateLimitError

    monkeypatch.setenv("CHAT_PROVIDERS", "cerebras,groq")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    llm_client.reset_client_for_tests()

    calls = {"n": 0}

    def side(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # cerebras → 429
            raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)
        raise APIConnectionError(request=None)  # groq → connection (retried then descends)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = side
        msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert msg is None
    assert kind == "rate_limit"
