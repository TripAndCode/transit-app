import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def app_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await pool.close()


@pytest.mark.asyncio
async def test_health_endpoint(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def _seed_admin(conn) -> str:
    """Insert an admin user + session, return the sid cookie value."""
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, 'admin') RETURNING user_id",
            f"admin{datetime.now().timestamp()}@x",
        )
    )["user_id"]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid


@pytest.fixture
async def agencies_client(apply_schema):
    from api.main import app
    import api.routers.agencies as _agencies_mod

    # Patch validate_feed_url so example.com / test URLs pass;
    # the real validator's SSRF logic is tested explicitly via file:// and 127.0.0.1.
    _orig = _agencies_mod.validate_feed_url
    _agencies_mod.validate_feed_url = lambda url: None

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    truncate_sql = (
        "TRUNCATE agencies, updates, static_stops, static_stop_times, "
        "static_trips, static_routes, static_calendar_dates, "
        "agg_route_stats, agg_route_hour, agg_route_dow, "
        "agg_daily_trend, agg_stop_seq, rag_chunks, agg_meta, sessions, users CASCADE"
    )
    # Pre-truncate so each test starts from a known-empty state — otherwise
    # data left over from `make seed-agencies` (or a parallel session) would
    # break test_list_agencies_empty's `[] == response` assertion.
    async with pool.acquire() as conn:
        await conn.execute(truncate_sql)
        admin_sid = await _seed_admin(conn)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # creating agencies is admin-only (the feed_url is an SSRF sink), so every
        # test that creates one passes this admin session cookie.
        yield client, admin_sid
        async with pool.acquire() as conn:
            await conn.execute(truncate_sql)
    await pool.close()
    _agencies_mod.validate_feed_url = _orig  # restore


@pytest.fixture
async def agencies_client_real_validator(apply_schema):
    """Like agencies_client but does NOT mock validate_feed_url."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    truncate_sql = (
        "TRUNCATE agencies, updates, static_stops, static_stop_times, "
        "static_trips, static_routes, static_calendar_dates, "
        "agg_route_stats, agg_route_hour, agg_route_dow, "
        "agg_daily_trend, agg_stop_seq, rag_chunks, agg_meta, sessions, users CASCADE"
    )
    async with pool.acquire() as conn:
        await conn.execute(truncate_sql)
        admin_sid = await _seed_admin(conn)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, admin_sid
        async with pool.acquire() as conn:
            await conn.execute(truncate_sql)
    await pool.close()


@pytest.mark.asyncio
async def test_list_agencies_empty(agencies_client):
    client, _ = agencies_client
    resp = await client.get("/api/agencies")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_agency(agencies_client):
    client, sid = agencies_client
    payload = {"agency_name": "Aomori Bus", "feed_url": "http://aomori.example.com"}
    resp = await client.post("/api/agencies", json=payload, headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    assert resp.status_code == 201
    data = resp.json()
    assert "agency_id" in data
    assert data["agency_name"] == "Aomori Bus"
    assert data["static_url"] is None


@pytest.mark.asyncio
async def test_create_agency_requires_admin(agencies_client):
    """No admin session → the route is locked (the feed_url is a server-side fetch sink)."""
    client, _ = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "nope", "feed_url": "http://x.example.com"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code in (401, 403)
    listing = await client.get("/api/agencies")
    assert all(a["agency_name"] != "nope" for a in listing.json())


@pytest.mark.asyncio
async def test_get_agency(agencies_client):
    client, sid = agencies_client
    payload = {"agency_name": "Test Agency", "feed_url": "http://test2.example.com"}
    create_resp = await client.post(
        "/api/agencies", json=payload, headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid}
    )
    aid = create_resp.json()["agency_id"]
    resp = await client.get(f"/api/agencies/{aid}")
    assert resp.status_code == 200
    assert resp.json()["agency_id"] == aid


@pytest.mark.asyncio
async def test_get_agency_not_found(agencies_client):
    client, _ = agencies_client
    resp = await client.get("/api/agencies/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_agencies_returns_multiple(agencies_client):
    client, sid = agencies_client
    for name, url in (("A", "http://a.example.com"), ("B", "http://b.example.com")):
        await client.post(
            "/api/agencies",
            json={"agency_name": name, "feed_url": url},
            headers={"Origin": TEST_ORIGIN},
            cookies={"sid": sid},
        )
    resp = await client.get("/api/agencies")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_create_agency_rejects_cross_origin(agencies_client):
    """An admin with a cross-origin POST still fails csrf_guard (403)."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "evil", "feed_url": "http://evil.example.com/feed.pb"},
        headers={"Origin": "https://evil.example.com"},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403
    listing = await client.get("/api/agencies")
    assert all(a["agency_name"] != "evil" for a in listing.json())


@pytest.mark.asyncio
async def test_create_agency_rejects_path_suffixed_origin(agencies_client):
    """Origin with a path is RFC-invalid and must not collapse to the trusted base."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "evil", "feed_url": "http://evil.example.com/feed.pb"},
        headers={"Origin": f"{TEST_ORIGIN}/.evil"},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "bad_origin",
    [
        f"{TEST_ORIGIN}?evil=1",  # query in Origin
        "http://user:pass@test",  # userinfo in Origin
    ],
)
@pytest.mark.asyncio
async def test_create_agency_rejects_malformed_origin(agencies_client, bad_origin):
    """Origin headers carrying query / userinfo are RFC-invalid → 403."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "x", "feed_url": "https://x/y"},
        headers={"Origin": bad_origin},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_agency_accepts_uppercase_scheme(agencies_client):
    """Schemes are case-insensitive per RFC 3986; uppercase Origin normalises."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "Caps", "feed_url": "https://caps/feed"},
        headers={"Origin": TEST_ORIGIN.upper()},
        cookies={"sid": sid},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_patch_agency_name(agencies_client):
    client, sid = agencies_client
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "Original", "feed_url": "http://x.example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    aid = create_resp.json()["agency_id"]
    resp = await client.patch(
        f"/api/agencies/{aid}",
        json={"agency_name": "Updated"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 200
    assert resp.json()["agency_name"] == "Updated"


@pytest.mark.asyncio
async def test_soft_delete_and_restore(agencies_client):
    client, sid = agencies_client
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "DeleteMe", "feed_url": "http://d.example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    aid = create_resp.json()["agency_id"]

    # Soft-delete
    del_resp = await client.delete(
        f"/api/agencies/{aid}",
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert del_resp.status_code == 204

    # Not in public list
    list_resp = await client.get("/api/agencies")
    assert not any(a["agency_id"] == aid for a in list_resp.json())

    # Still in admin list
    admin_resp = await client.get("/api/admin/agencies", cookies={"sid": sid})
    assert admin_resp.status_code == 200
    assert any(a["agency_id"] == aid and a["deleted_at"] is not None for a in admin_resp.json())

    # Restore
    restore_resp = await client.post(
        f"/api/agencies/{aid}/restore",
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["deleted_at"] is None

    # Back in public list
    list_resp2 = await client.get("/api/agencies")
    assert any(a["agency_id"] == aid for a in list_resp2.json())


@pytest.mark.asyncio
async def test_patch_feed_url_rejects_file_scheme(agencies_client_real_validator):
    client, sid = agencies_client_real_validator
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "FeedTest", "feed_url": "http://example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert create_resp.status_code == 201, create_resp.text
    aid = create_resp.json()["agency_id"]
    resp = await client.patch(
        f"/api/agencies/{aid}",
        json={"feed_url": "file:///etc/passwd"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_feed_url_rejects_loopback(agencies_client_real_validator):
    client, sid = agencies_client_real_validator
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "LoopTest", "feed_url": "http://example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert create_resp.status_code == 201, create_resp.text
    aid = create_resp.json()["agency_id"]
    resp = await client.patch(
        f"/api/agencies/{aid}",
        json={"feed_url": "http://127.0.0.1/feed"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_unknown_ingest_strategy(agencies_client):
    client, sid = agencies_client
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "StratTest", "feed_url": "http://s.example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    aid = create_resp.json()["agency_id"]
    resp = await client.patch(
        f"/api/agencies/{aid}",
        json={"ingest_strategy": "nonexistent_strategy"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 422


async def _audit_count(agency_id: int, kind: str) -> int:
    from api.main import app

    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM login_events WHERE kind=$1 AND meta->>'agency_id' = $2",
            kind,
            str(agency_id),
        )
    return row["n"]


@pytest.mark.asyncio
async def test_soft_delete_idempotent(agencies_client):
    client, sid = agencies_client
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "Idem", "feed_url": "http://i.example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    aid = create_resp.json()["agency_id"]
    await client.delete(f"/api/agencies/{aid}", headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    resp2 = await client.delete(f"/api/agencies/{aid}", headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    assert resp2.status_code == 204  # idempotent
    # The guard (tag == "UPDATE 1") must stop the second delete from writing
    # a duplicate audit row — this is the behavior the idempotency is for.
    assert await _audit_count(aid, "agency_deleted") == 1


@pytest.mark.asyncio
async def test_restore_idempotent(agencies_client):
    client, sid = agencies_client
    create_resp = await client.post(
        "/api/agencies",
        json={"agency_name": "RestoreIdem", "feed_url": "http://ri.example.com"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    aid = create_resp.json()["agency_id"]
    await client.delete(f"/api/agencies/{aid}", headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})

    resp1 = await client.post(f"/api/agencies/{aid}/restore", headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    resp2 = await client.post(f"/api/agencies/{aid}/restore", headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    assert resp1.status_code == 200
    assert resp2.status_code == 200  # restoring an already-active agency is a no-op, not an error
    assert resp2.json()["deleted_at"] is None
    # Re-restoring an already-active agency must not write a second audit row.
    assert await _audit_count(aid, "agency_restored") == 1


@pytest.mark.asyncio
async def test_create_agency_rejects_internal_ip(agencies_client_real_validator):
    client, sid = agencies_client_real_validator
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "SSRF", "feed_url": "http://192.168.1.1/feed"},
        headers={"Origin": TEST_ORIGIN},
        cookies={"sid": sid},
    )
    assert resp.status_code == 422
