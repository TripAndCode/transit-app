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
    assert set(body) == {"auth_enabled", "local_admin_enabled"}
    assert isinstance(body["auth_enabled"], bool)
    assert isinstance(body["local_admin_enabled"], bool)
