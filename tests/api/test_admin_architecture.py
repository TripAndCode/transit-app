"""Tests for GET /api/admin/architecture/docs[/{slug}] -- the developer-only
`/admin/architecture` page's backing endpoints.

Both routes are filesystem-only (no DB query), but still sit behind
`require_admin`, which itself needs a real session row to resolve
`request.state.user` -- so these still need the DB-backed `admin_client`
fixture, same pattern as `tests/api/test_routers_admin.py`, even though the
handlers themselves never touch `conn`.
"""

import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import _test_pool
from tests.fixtures.users import seed_session


@pytest.fixture
async def arch_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_docs_list_requires_admin(arch_client, aconn):
    sid = (await seed_session(aconn, role="user")).sid
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
    sid = (await seed_session(aconn, role="admin")).sid
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
    sid = (await seed_session(aconn, role="user")).sid
    r = await arch_client.get("/api/admin/architecture/docs/ask-tab", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_doc_detail_returns_content(arch_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await arch_client.get("/api/admin/architecture/docs/ask-tab", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "ask-tab"
    assert body["title"]
    assert len(body["content"]) > 0


@pytest.mark.asyncio
async def test_doc_detail_unknown_slug_404s(arch_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await arch_client.get("/api/admin/architecture/docs/does-not-exist", cookies={"sid": sid})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_doc_detail_rejects_path_traversal(arch_client, aconn):
    """Slugs that would escape `docs/features/` if naively path-joined (a
    bare `..`, or a filename that exists elsewhere in the repo but not in
    that directory, like `CLAUDE`) must 404, not serve an arbitrary repo
    file -- proves the lookup is matched against the live enumeration of
    `docs/features/*.md` stems, never path-joined from the raw param."""
    sid = (await seed_session(aconn, role="admin")).sid
    for slug in ("..", "CLAUDE", "etc-passwd"):
        r = await arch_client.get(f"/api/admin/architecture/docs/{slug}", cookies={"sid": sid})
        assert r.status_code == 404, slug
