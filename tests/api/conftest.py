import pytest

from pipeline.query import llm_client


@pytest.fixture(autouse=True)
def _llm_key_present(monkeypatch):
    """A usable shared provider by default, so follow-up and config tests do not
    depend on the host's environment. Tests of the no-key case use ``no_llm_key``
    or set their own provider env."""
    monkeypatch.setenv("CHAT_PROVIDERS", "gemini,openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    llm_client.reset_client_for_tests()
    yield
    llm_client.reset_client_for_tests()


@pytest.fixture
def no_llm_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm_client.reset_client_for_tests()
    yield
    llm_client.reset_client_for_tests()
