from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.security import token_hash
from pipeline.flags import invalidate as invalidate_flags
from tests.conftest import TEST_ORIGIN, _test_pool


async def _seed_user_and_session(conn, *, role="user", llm_approved=False):
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, name, role, llm_approved) VALUES ($1, $2, $3, $4) RETURNING user_id",
            f"u{datetime.now().timestamp()}@x",
            "Yo",
            role,
            llm_approved,
        )
    )["user_id"]
    sid = f"sid-{uid:0>30}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at, user_agent) VALUES ($1, $2, $3, $4)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=30),
        "test-ua",
    )
    return sid, uid


@pytest.fixture
async def copilot_app(apply_schema):
    """Mirrors ``ask_app``: ``copilot_insight``'s ``agency_id: int =
    Depends(get_agency)`` always needs a real pool/row, mocked insight or not.
    """
    from api.main import app

    pool = await _test_pool()
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
            "agg_route_stats, agg_route_hour, "
            "agg_daily_trend, rag_chunks CASCADE"
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

    async def fake_insight(tab, view_payload, *, locale="ja"):
        return {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}


@pytest.mark.asyncio
async def test_copilot_insight_renders_the_template_from_the_posted_payload(copilot_client):
    """Unmocked: the route hands the posted view payload to the real renderer."""
    client, agency_id = copilot_client
    view_payload = {
        "headline": {"avg_min": 6.4, "baseline_avg_min": 4.1, "delta_pct": 56.1, "samples": 812},
        "top_delayed": {
            "routes": [{"route_code": "R12", "route_short_name": "12", "avg_min": 14.2}],
            "delayed_count": 1,
        },
    }
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": view_payload},
        headers={"Origin": TEST_ORIGIN, "Accept-Language": "en"},
    )
    assert resp.status_code == 200, resp.text
    assert "14.2" in resp.json()["text"]


@pytest.mark.asyncio
async def test_copilot_insight_rejects_empty_payload(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, view_payload, *, locale="ja"):
        from pipeline.query.copilot import NoInsightAvailable

        raise NoInsightAvailable("empty")

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_copilot_insight_rejects_cross_origin(copilot_client, monkeypatch):
    """Cross-origin POST to /copilot/insight returns 403 before any insight work."""
    client, agency_id = copilot_client

    async def must_not_be_called(tab, view_payload, *, locale="ja"):
        return {
            "text": "csrf_guard FAILED — request reached generate_proactive_insight",
            "cite": "x",
            "low_confidence": False,
        }

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", must_not_be_called)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {}},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_copilot_insight_serves_an_anonymous_caller(copilot_client, monkeypatch):
    """No LLM is involved, so the insight needs no signed-in, approved caller."""
    client, agency_id = copilot_client
    seen: list[str] = []

    async def fake_insight(tab, view_payload, *, locale="ja"):
        seen.append(tab)
        return {"text": "ok", "cite": "c", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    assert seen == ["overview"]


@pytest.mark.asyncio
async def test_copilot_insight_serves_an_unapproved_signed_in_caller(copilot_client, aconn, monkeypatch):
    """``users.llm_approved`` gates LLM calls only; this route makes none."""
    client, agency_id = copilot_client
    sid, _uid = await _seed_user_and_session(aconn, llm_approved=False)

    async def fake_insight(tab, view_payload, *, locale="ja"):
        return {"text": "ok", "cite": "c", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {"headline": {"samples": 1}}},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_copilot_insight_threads_accept_language_locale(copilot_client, monkeypatch):
    """The resolved request locale reaches generate_proactive_insight."""
    client, agency_id = copilot_client
    seen: list[str] = []

    async def fake_insight(tab, view_payload, *, locale="ja"):
        seen.append(locale)
        return {"text": "ok", "cite": "c", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    body = {"tab": "overview", "view_payload": {"headline": {"samples": 1}}}
    for header, expected in (("en", "en"), ("ja", "ja")):
        resp = await client.post(
            f"/api/{agency_id}/copilot/insight",
            json=body,
            headers={"Origin": TEST_ORIGIN, "Accept-Language": header},
        )
        assert resp.status_code == 200
        assert seen[-1] == expected


async def _must_not_run(tab, view_payload, *, locale="ja"):
    raise AssertionError("generate_proactive_insight must not be reached while the feature is off")


@pytest.mark.asyncio
async def test_copilot_insight_returns_503_when_disabled(copilot_client, monkeypatch):
    """The kill switch short-circuits before any insight work."""
    client, agency_id = copilot_client
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "false")
    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", _must_not_run)

    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "view_payload": {"headline": {"samples": 1}}},
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
    invalidate_flags()
    resp = await client.get(f"/api/{agency_id}/copilot/enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}

    # The flag layer caches its resolution process-wide, so an env change
    # mid-test is only visible once the cache is dropped -- which is what a
    # PATCH to /admin/flags does in production.
    monkeypatch.setenv("COPILOT_INSIGHT_ENABLED", "false")
    invalidate_flags()
    resp = await client.get(f"/api/{agency_id}/copilot/enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
