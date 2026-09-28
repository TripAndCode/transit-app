"""Every provider call is time-bounded and leaves a usage record.

The fallback ladder only helps while the caller is still waiting: a rung that
hangs for the SDK's default read timeout never hands over to the next one in
time. Both client constructors (the shared ladder and the BYOK one-off path)
therefore pass :data:`pipeline.query.llm_client.REQUEST_TIMEOUT`.

The usage line is the only per-call cost signal the app has; it carries token
counts and provider/model names, never key material or message content.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openai
import pytest

from pipeline.query import chat, llm_client

SECRET = "sk-live-thisistheoperators-secret-key"
PROMPT_TEXT = "一番遅れている路線は？"


@pytest.fixture(autouse=True)
def _reset():
    llm_client.reset_client_for_tests()
    yield
    llm_client.reset_client_for_tests()


def _gemini_only(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


def _response(usage):
    return MagicMock(choices=[MagicMock(message=MagicMock(content="ok"))], usage=usage)


def _usage(prompt=120, completion=7, cached=64):
    details = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=details)


def test_request_timeout_is_shorter_than_the_sdk_default():
    assert llm_client.REQUEST_TIMEOUT.read is not None
    assert llm_client.REQUEST_TIMEOUT.read < openai.DEFAULT_TIMEOUT.read
    assert llm_client.REQUEST_TIMEOUT.connect is not None


def test_ladder_client_is_built_with_the_request_timeout(monkeypatch):
    _gemini_only(monkeypatch)
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _response(_usage())
        llm_client.LLMClient().chat_completions(messages=[{"role": "user", "content": PROMPT_TEXT}])
    _, kwargs = mock_openai.call_args
    assert kwargs["timeout"] is llm_client.REQUEST_TIMEOUT


def test_byok_client_is_built_with_the_request_timeout():
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _response(_usage())
        chat._completion_with_key("gemini", SECRET, messages=[{"role": "user", "content": PROMPT_TEXT}])
    _, kwargs = mock_openai.call_args
    assert kwargs["timeout"] is llm_client.REQUEST_TIMEOUT


def _usage_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("llm usage")]


@pytest.mark.parametrize(
    ("cached", "cached_text"),
    [(64, "cached_tokens=64"), (None, "cached_tokens=None")],
)
def test_ladder_logs_token_usage_without_key_or_content(monkeypatch, caplog, cached, cached_text):
    _gemini_only(monkeypatch)
    with caplog.at_level(logging.INFO, logger="pipeline.query.llm_client"):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = _response(_usage(cached=cached))
            llm_client.LLMClient().chat_completions(messages=[{"role": "user", "content": PROMPT_TEXT}])
    lines = _usage_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    for fragment in (
        "provider=gemini",
        "model=gemini-3.1-flash-lite",
        "prompt_tokens=120",
        cached_text,
        "completion_tokens=7",
    ):
        assert fragment in line
    assert SECRET not in caplog.text
    assert PROMPT_TEXT not in caplog.text


def test_byok_call_logs_token_usage_without_key(caplog, monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with caplog.at_level(logging.INFO, logger="pipeline.query.llm_client"):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = _response(_usage(prompt=9, completion=3))
            chat._completion_with_key("openai", SECRET, messages=[{"role": "user", "content": PROMPT_TEXT}])
    lines = _usage_lines(caplog)
    assert len(lines) == 1
    assert "provider=openai" in lines[0]
    assert "model=gpt-5.4-mini" in lines[0]
    assert "prompt_tokens=9" in lines[0]
    assert SECRET not in caplog.text
    assert PROMPT_TEXT not in caplog.text


def test_missing_usage_is_not_an_error(monkeypatch, caplog):
    _gemini_only(monkeypatch)
    with caplog.at_level(logging.INFO, logger="pipeline.query.llm_client"):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = _response(None)
            msg, kind = llm_client.LLMClient().chat_completions(messages=[])
    assert kind is None
    assert msg is not None
    assert _usage_lines(caplog) == []
