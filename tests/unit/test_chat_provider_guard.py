"""_allowed_providers() gates the primary Ask path's LLM provider ladder.

Unlike pipeline/query/followup.py (which defaults to Cerebras-only because
Groq was found to obey prompt injection), the primary Ask path's documented
default is Groq (chat.py's module docstring: "the historical default of Groq
is preserved when CHAT_PROVIDERS is unset") - restricting it the same way
would silently change cost/latency/answer-quality for the main feature.
ASK_CHAT_ALLOWED_PROVIDERS is opt-in: unset means "no restriction"
(today's behavior, unchanged), and operators can widen the guard once they
choose to trade off cost/latency for injection resistance.
"""

from pipeline.query import chat as chat_module


def test_default_unset_means_no_restriction(monkeypatch):
    monkeypatch.delenv("ASK_CHAT_ALLOWED_PROVIDERS", raising=False)
    assert chat_module._allowed_providers() is None


def test_empty_string_means_no_restriction(monkeypatch):
    monkeypatch.setenv("ASK_CHAT_ALLOWED_PROVIDERS", "")
    assert chat_module._allowed_providers() is None


def test_configured_value_restricts_to_named_providers(monkeypatch):
    monkeypatch.setenv("ASK_CHAT_ALLOWED_PROVIDERS", "cerebras, openai")
    assert chat_module._allowed_providers() == {"cerebras", "openai"}


def test_comma_only_value_means_no_restriction_not_empty_set(monkeypatch):
    """A value like "," or " , " passes the not-raw guard but splits into
    zero real names - must fall back to "no restriction", not an empty
    set that would zero out the whole provider ladder on a misconfig."""
    monkeypatch.setenv("ASK_CHAT_ALLOWED_PROVIDERS", " , ")
    assert chat_module._allowed_providers() is None
