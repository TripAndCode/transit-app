"""LLMClient provider-fallback unit tests."""

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
    result = client.chat_completions(messages=[{"role": "user", "content": "hi"}])
    assert result is None


def test_missing_api_key_provider_skipped(monkeypatch):
    """Provider listed but no API key set → drop from ladder."""
    _set_providers(monkeypatch, providers="cerebras,groq",
                   GROQ_API_KEY="real-groq")
    client = llm_client.LLMClient()
    names = [p.name for p in client.providers()]
    assert names == ["groq"]  # cerebras dropped


def test_first_provider_success(monkeypatch):
    """Cerebras returns a message → groq never called."""
    _set_providers(monkeypatch, providers="cerebras,groq",
                   CEREBRAS_API_KEY="c", GROQ_API_KEY="g")
    fake_message = MagicMock(name="message", content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    with patch("openai.OpenAI") as mock_openai:
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = fake_response
        out = llm_client.LLMClient().chat_completions(messages=[])
    assert out is fake_message
    assert mock_openai.call_count == 1
    args, kwargs = mock_openai.call_args
    assert kwargs.get("base_url") == "https://api.cerebras.ai/v1"


def test_first_provider_rate_limited_falls_back(monkeypatch):
    """Cerebras 429 → groq used."""
    from openai import RateLimitError
    _set_providers(monkeypatch, providers="cerebras,groq",
                   CEREBRAS_API_KEY="c", GROQ_API_KEY="g")
    fake_message = MagicMock(content="ok")
    fake_response = MagicMock(choices=[MagicMock(message=fake_message)])

    call_count = {"n": 0}
    def fake_create(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RateLimitError(
                message="429", response=MagicMock(status_code=429), body=None
            )
        return fake_response

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = fake_create
        out = llm_client.LLMClient().chat_completions(messages=[])
    assert out is fake_message
    assert call_count["n"] == 2   # cerebras failed, groq succeeded


def test_all_providers_rate_limited_returns_none(monkeypatch):
    from openai import RateLimitError
    _set_providers(monkeypatch, providers="cerebras,groq",
                   CEREBRAS_API_KEY="c", GROQ_API_KEY="g")

    def always_429(*a, **kw):
        raise RateLimitError(message="429", response=MagicMock(status_code=429), body=None)

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = always_429
        out = llm_client.LLMClient().chat_completions(messages=[])
    assert out is None


def test_ollama_does_not_require_api_key(monkeypatch):
    """Ollama provider should appear in the ladder even with no API key set."""
    _set_providers(monkeypatch, providers="ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    client = llm_client.LLMClient()
    names = [p.name for p in client.providers()]
    assert names == ["ollama"]


def test_per_provider_base_url_override(monkeypatch):
    """Operator can override base_url via env."""
    _set_providers(monkeypatch, providers="cerebras",
                   CEREBRAS_API_KEY="c",
                   CEREBRAS_BASE_URL="https://example.test/v1")
    client = llm_client.LLMClient()
    assert client.providers()[0].base_url == "https://example.test/v1"
