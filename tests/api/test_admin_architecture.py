"""Tests for GET /api/admin/architecture/docs[/{slug}] -- the developer-only
`/admin/architecture` page's backing endpoints (item 25).

Both routes are filesystem-only (no DB query), but still sit behind
`require_admin`, which itself needs a real session row to resolve
`request.state.user` -- so these still need the DB-backed `admin_client`
fixture, same pattern as `tests/api/test_routers_admin.py`, even though the
handlers themselves never touch `conn`.
"""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


async def _seed(conn, *, role="user"):
    email = f"arch{datetime.now().timestamp()}@x"
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, $2) RETURNING user_id",
            email,
            role,
        )
    )["user_id"]
    sid = f"sid-arch-{uid}"
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid


@pytest.fixture
async def arch_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_docs_list_requires_admin(arch_client, aconn):
    sid = await _seed(aconn, role="user")
    r = await arch_client.get("/api/admin/architecture/docs", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_docs_list_anonymous_unauthorized(arch_client):
    r = await arch_client.get("/api/admin/architecture/docs")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_docs_list_returns_known_feature_docs(arch_client, aconn):
    """Enumeration is dynamic (glob), not a hardcoded list -- assert against
    real repo files that must exist (ask-tab.md predates this item)."""
    sid = await _seed(aconn, role="admin")
    r = await arch_client.get("/api/admin/architecture/docs", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    slugs = {d["slug"] for d in body}
    assert "ask-tab" in slugs
    assert "map-tab" in slugs
    # Every entry has a non-empty title (falls back to the slug if the file
    # has no leading `# ` heading).
    for d in body:
        assert d["title"]


@pytest.mark.asyncio
async def test_doc_detail_requires_admin(arch_client, aconn):
    sid = await _seed(aconn, role="user")
    r = await arch_client.get("/api/admin/architecture/docs/ask-tab", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_doc_detail_returns_content(arch_client, aconn):
    sid = await _seed(aconn, role="admin")
    r = await arch_client.get("/api/admin/architecture/docs/ask-tab", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "ask-tab"
    assert body["title"]
    assert len(body["content"]) > 0


@pytest.mark.asyncio
async def test_doc_detail_unknown_slug_404s(arch_client, aconn):
    sid = await _seed(aconn, role="admin")
    r = await arch_client.get("/api/admin/architecture/docs/does-not-exist", cookies={"sid": sid})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_doc_detail_rejects_path_traversal(arch_client, aconn):
    """Slugs that would escape `docs/features/` if naively path-joined (a
    bare `..`, or a filename that exists elsewhere in the repo but not in
    that directory, like `CLAUDE`) must 404, not serve an arbitrary repo
    file -- proves the lookup is matched against the live enumeration of
    `docs/features/*.md` stems, never path-joined from the raw param."""
    sid = await _seed(aconn, role="admin")
    for slug in ("..", "CLAUDE", "etc-passwd"):
        r = await arch_client.get(f"/api/admin/architecture/docs/{slug}", cookies={"sid": sid})
        assert r.status_code == 404, slug
