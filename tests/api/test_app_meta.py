"""Shape of the two app-level endpoints the SPA reads before anything else."""

import pytest


@pytest.mark.asyncio
async def test_health_reports_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_config_exposes_both_auth_switches(client):
    """The SPA hides the SSO buttons and the break-glass local-admin form
    independently, so dropping either key silently re-reveals one of them."""
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"auth_enabled", "local_admin_enabled", "login_required", "feedback_url", "llm_enabled"}
    assert isinstance(body["login_required"], bool)
    assert isinstance(body["auth_enabled"], bool)
    assert isinstance(body["local_admin_enabled"], bool)
    assert isinstance(body["llm_enabled"], bool)


@pytest.mark.asyncio
async def test_config_carries_no_feedback_address_unless_the_deployment_sets_one(client, monkeypatch):
    monkeypatch.delenv("FEEDBACK_URL", raising=False)
    assert (await client.get("/api/config")).json()["feedback_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["https://forms.example.test/feedback", "mailto:feedback@example.test", "HTTPS://forms.example.test/feedback"],
)
async def test_config_hands_the_spa_an_https_or_mailto_feedback_address(client, monkeypatch, url):
    monkeypatch.setenv("FEEDBACK_URL", url)
    assert (await client.get("/api/config")).json()["feedback_url"] == url


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://forms.example.test/feedback", " "])
async def test_config_drops_a_feedback_address_the_spa_should_not_link_to(client, monkeypatch, url):
    """The SPA renders it as a link, so only an https or mailto address goes out."""
    monkeypatch.setenv("FEEDBACK_URL", url)
    assert (await client.get("/api/config")).json()["feedback_url"] is None


@pytest.fixture
def _fresh_llm_client():
    from pipeline import flags
    from pipeline.query import llm_client

    llm_client.reset_client_for_tests()
    flags.invalidate()
    yield
    llm_client.reset_client_for_tests()
    flags.invalidate()


@pytest.mark.asyncio
async def test_config_reports_llm_disabled_without_a_provider_key(client, monkeypatch, _fresh_llm_client):
    monkeypatch.setenv("CHAT_PROVIDERS", "gemini,openai")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (await client.get("/api/config")).json()["llm_enabled"] is False


@pytest.mark.asyncio
async def test_config_reports_llm_enabled_with_a_provider_key(client, monkeypatch, _fresh_llm_client):
    monkeypatch.setenv("CHAT_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert (await client.get("/api/config")).json()["llm_enabled"] is True


@pytest.mark.asyncio
async def test_config_reports_llm_disabled_when_the_operator_kill_switch_is_off(client, monkeypatch, _fresh_llm_client):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("ASK_LLM_ENABLED", "false")
    assert (await client.get("/api/config")).json()["llm_enabled"] is False
