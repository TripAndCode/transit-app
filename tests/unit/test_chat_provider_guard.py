"""_allowed_providers() gates the primary Ask path's LLM provider ladder.

Unlike pipeline/query/followup.py (which defaults to Cerebras-only because
Groq was found to obey prompt injection), the primary Ask path's documented
default is Groq (chat.py's module docstring: "the historical default of Groq
is preserved when CHAT_PROVIDERS is unset") - restricting it the same way
would silently change cost/latency/answer-quality for the main feature.
ASK_CHAT_PROVIDERS_PREFER_SAFE is opt-in: unset means "no restriction"
(today's behavior, unchanged), and operators can widen the guard once they
choose to trade off cost/latency for injection resistance.
"""

from pipeline.query import chat as chat_module


def test_default_unset_means_no_restriction(monkeypatch):
    monkeypatch.delenv("ASK_CHAT_PROVIDERS_PREFER_SAFE", raising=False)
    assert chat_module._allowed_providers() is None


def test_empty_string_means_no_restriction(monkeypatch):
    monkeypatch.setenv("ASK_CHAT_PROVIDERS_PREFER_SAFE", "")
    assert chat_module._allowed_providers() is None


def test_configured_value_restricts_to_named_providers(monkeypatch):
    monkeypatch.setenv("ASK_CHAT_PROVIDERS_PREFER_SAFE", "cerebras, openai")
    assert chat_module._allowed_providers() == {"cerebras", "openai"}
