import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def copilot_app(apply_schema):
    """Mirrors ``ask_app``: ``copilot_insight``'s ``agency_id: int =
    Depends(get_agency)`` always needs a real pool/row, mocked LLM call or not.
    """
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Test Agency",
        "http://test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_stop_seq, rag_chunks CASCADE"
        )
    await pool.close()


@pytest.fixture
async def copilot_client(copilot_app):
    app, agency_id = copilot_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.fixture(autouse=True)
def _copilot_enabled(monkeypatch):
    """The feature ships off by default, so every behaviour test turns it on.

    The disabled-path tests below override this back to a falsy value.
    """
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "true")


@pytest.mark.asyncio
async def test_copilot_insight_returns_rendered_text(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
        return {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}


@pytest.mark.asyncio
async def test_copilot_insight_rejects_empty_payload(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
        from pipeline.query.copilot import NoInsightAvailable

        raise NoInsightAvailable("empty")

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_copilot_insight_rejects_cross_origin(copilot_client, monkeypatch):
    """Cross-origin POST to /copilot/insight returns 403 even before reaching the LLM."""
    client, agency_id = copilot_client

    async def must_not_be_called(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
        return {
            "text": "csrf_guard FAILED — request reached generate_proactive_insight",
            "cite": "x",
            "low_confidence": False,
        }

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", must_not_be_called)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {}},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_copilot_insight_enforces_anon_quota(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
        return {"text": "x", "cite": "y", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    monkeypatch.setenv("COPILOT_ANON_DAILY_LIMIT", "0")
    from api.middleware.ratelimit import reset_anon_quota_for_tests

    reset_anon_quota_for_tests()
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "copilot_anon_quota_exceeded"


@pytest.mark.asyncio
async def test_copilot_insight_threads_accept_language_locale(copilot_client, monkeypatch):
    """The resolved request locale reaches generate_proactive_insight."""
    client, agency_id = copilot_client
    seen: list[str] = []

    async def fake_insight(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
        seen.append(locale)
        return {"text": "ok", "cite": "c", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    body = {"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}}
    for header, expected in (("en", "en"), ("ja", "ja")):
        resp = await client.post(
            f"/api/{agency_id}/copilot/insight",
            json=body,
            headers={"Origin": TEST_ORIGIN, "Accept-Language": header},
        )
        assert resp.status_code == 200
        assert seen[-1] == expected


async def _must_not_run(tab, filters, view_payload, *, locale="ja", conn=None, user_id=None):
    raise AssertionError("generate_proactive_insight must not be reached while the feature is off")


@pytest.mark.asyncio
async def test_copilot_insight_returns_503_when_disabled(copilot_client, monkeypatch):
    """The kill switch short-circuits before any quota or LLM work."""
    client, agency_id = copilot_client
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "false")
    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", _must_not_run)

    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 503
    # Pin the body too: without this the test's own assertion never separates
    # "the gate returned 503" from "something downstream happened to raise".
    assert resp.json()["detail"] == "copilot_disabled"


@pytest.mark.asyncio
async def test_copilot_enabled_endpoint_reports_the_flag(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "true")
    resp = await client.get(f"/api/{agency_id}/copilot/enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}

    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "false")
    resp = await client.get(f"/api/{agency_id}/copilot/enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


@pytest.mark.asyncio
async def test_disabled_copilot_does_not_consume_anon_quota(copilot_client, monkeypatch):
    """A disabled feature must not bill the caller's daily budget."""
    client, agency_id = copilot_client
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "false")
    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", _must_not_run)

    consumed: list[str] = []
    # Patched where it is looked up, not on the defining module: the router
    # imported the name directly, so its own binding is what runs.
    monkeypatch.setattr(
        "api.routers.copilot.check_and_consume_anon_quota",
        lambda *a, **k: consumed.append("hit") or True,
    )

    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 503
    assert consumed == []
