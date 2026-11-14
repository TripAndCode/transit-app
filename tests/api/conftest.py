import pytest


@pytest.fixture
def no_llm_key(monkeypatch):
    from pipeline.query import llm_client

    monkeypatch.setenv("CHAT_PROVIDERS", "gemini,openai")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm_client.reset_client_for_tests()
    yield
    llm_client.reset_client_for_tests()
