import pytest

from pipeline.flags import invalidate as invalidate_flags
from pipeline.query import llm_client

_SSO = {
    "SESSION_SIGNING_KEY": "test-signing-key",
    "GOOGLE_CLIENT_ID": "g",
    "GOOGLE_CLIENT_SECRET": "gs",
    "GITHUB_CLIENT_ID": "h",
    "GITHUB_CLIENT_SECRET": "hs",
}


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


@pytest.fixture
def sso_on(monkeypatch):
    for key, value in _SSO.items():
        monkeypatch.setenv(key, value)
    invalidate_flags()
