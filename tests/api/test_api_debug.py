"""Tests for the env-gated perf debug endpoints.

Contract:
1. GET  /api/debug/perf  -> 200; body has ops, caches, pool {size, idle}.
2. POST /api/debug/perf/reset (authenticated admin, same-origin) -> 200;
   subsequent GET shows ops == {}.
3. POST /api/debug/perf/reset requires an authenticated admin and is
   CSRF-guarded: anonymous -> 401, non-admin -> 403, cross-origin admin -> 403.
4. PERF_DEBUG_ENABLED=false -> 404 on both endpoints, for every caller.
5. No env var set (default) -> 404 on both endpoints (fail-closed).

The env gate outranks the auth gate: a disabled surface answers 404 even to
an anonymous caller, so a prober cannot tell the route apart from one that
does not exist.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from pipeline import perf
from tests.conftest import TEST_ORIGIN, _test_pool


@pytest.fixture(autouse=True)
def reset_perf():
    """Ensure a clean perf registry before and after every test."""
    perf.reset()
    yield
    perf.reset()


async def _seed_session(aconn, *, role="admin"):
    email = f"{role}-{datetime.now().timestamp()}@x"
    uid = (await aconn.fetchrow("INSERT INTO users (email, role) VALUES ($1, $2) RETURNING user_id", email, role))[
        "user_id"
    ]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await aconn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=30),
    )
    return sid


@pytest.fixture
async def debug_client(apply_schema, monkeypatch):
    """Yield an HTTPX client wired to the FastAPI app with a fresh pool.

    The pool is created and closed per-test so concurrent tests cannot
    share or step on ``app.state.pool``, matching the pattern in conftest.

    PERF_DEBUG_ENABLED is set to "true" here so the enabled-path tests work
    with the new fail-closed default.
    """
    monkeypatch.setenv("PERF_DEBUG_ENABLED", "true")

    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await pool.close()


@pytest.mark.asyncio
async def test_perf_snapshot(debug_client):
    """GET /api/debug/perf returns 200 with ops, caches, and pool keys."""
    # Record a label so ops is non-empty and verifiable.
    perf.record("test.label", 42.0)

    resp = await debug_client.get("/api/debug/perf")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "ops" in body
    assert "caches" in body
    assert "pool" in body

    assert "test.label" in body["ops"]
    pool_info = body["pool"]
    assert "size" in pool_info
    assert "idle" in pool_info


@pytest.mark.asyncio
async def test_perf_reset(debug_client, aconn):
    """POST /api/debug/perf/reset (admin, same-origin) clears the registry;
    subsequent GET shows ops == {}."""
    perf.record("pre.reset", 10.0)
    sid = await _seed_session(aconn, role="admin")

    reset_resp = await debug_client.post(
        "/api/debug/perf/reset",
        cookies={"sid": sid},
        headers={"Origin": TEST_ORIGIN},
    )
    assert reset_resp.status_code == 200
    assert reset_resp.json() == {"status": "reset"}

    snap_resp = await debug_client.get("/api/debug/perf")
    assert snap_resp.status_code == 200
    body = snap_resp.json()
    assert body["ops"] == {}


@pytest.mark.asyncio
async def test_perf_reset_requires_authentication(debug_client):
    """An anonymous caller is rejected before the handler ever runs."""
    resp = await debug_client.post("/api/debug/perf/reset", headers={"Origin": TEST_ORIGIN})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_perf_reset_requires_admin_role(debug_client, aconn):
    """A signed-in non-admin is forbidden."""
    sid = await _seed_session(aconn, role="user")
    resp = await debug_client.post(
        "/api/debug/perf/reset",
        cookies={"sid": sid},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_perf_reset_rejects_cross_origin(debug_client, aconn):
    """An authenticated admin still fails csrf_guard on a cross-origin POST."""
    sid = await _seed_session(aconn, role="admin")
    resp = await debug_client.post(
        "/api/debug/perf/reset",
        cookies={"sid": sid},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_perf_disabled(monkeypatch, debug_client):
    """Both endpoints return 404 when PERF_DEBUG_ENABLED=false, including for
    an anonymous POST — the env gate runs ahead of the auth dependency, so a
    disabled surface never answers 401 and never advertises its existence."""
    monkeypatch.setenv("PERF_DEBUG_ENABLED", "false")

    get_resp = await debug_client.get("/api/debug/perf")
    assert get_resp.status_code == 404

    post_resp = await debug_client.post("/api/debug/perf/reset", headers={"Origin": TEST_ORIGIN})
    assert post_resp.status_code == 404


@pytest.mark.asyncio
async def test_perf_disabled_404s_an_authenticated_admin_too(monkeypatch, debug_client, aconn):
    """Past the auth/CSRF gates, a disabled surface still 404s for an admin."""
    monkeypatch.setenv("PERF_DEBUG_ENABLED", "false")
    sid = await _seed_session(aconn, role="admin")

    resp = await debug_client.post(
        "/api/debug/perf/reset",
        cookies={"sid": sid},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_perf_default_is_closed(apply_schema, monkeypatch):
    """With no PERF_DEBUG_ENABLED env var set, both endpoints return 404.

    This verifies the fail-closed default: the surface must be explicitly
    enabled in dev; it must never be reachable on a fresh/production deploy
    that hasn't set the env var, and must not distinguish itself from a
    nonexistent route by answering 401 to an anonymous caller.
    """
    monkeypatch.delenv("PERF_DEBUG_ENABLED", raising=False)

    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            get_resp = await client.get("/api/debug/perf")
            assert get_resp.status_code == 404

            post_resp = await client.post("/api/debug/perf/reset", headers={"Origin": TEST_ORIGIN})
            assert post_resp.status_code == 404
    finally:
        await pool.close()
