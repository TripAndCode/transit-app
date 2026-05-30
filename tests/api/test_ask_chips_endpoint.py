"""/ask/build-schema chips extension + /ask/popular-chips tests."""
from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def chips_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
    app.state.pool = pool
    yield app, row["agency_id"], pool
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_build_schema_includes_chips(chips_app):
    app, agency_id, _ = chips_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/build-schema")
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body
    assert "chips" in body
    # Chips grouped by category, total 26 entries
    chips_by_cat = body["chips"]
    assert isinstance(chips_by_cat, dict)
    total = sum(len(v) for v in chips_by_cat.values())
    assert total == 26
    # Order of category keys matches the spec: meta / ranking / trend / compare / detail
    assert list(chips_by_cat.keys()) == ["meta", "ranking", "trend", "compare", "detail"]
    # Each chip has id, title (localized), tool, args, builder_required
    sample = chips_by_cat["ranking"][0]
    for k in ("id", "title", "tool", "args", "builder_required"):
        assert k in sample


@pytest.mark.asyncio
async def test_build_schema_localizes_chip_titles_ja(chips_app):
    app, agency_id, _ = chips_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/build-schema",
                        headers={"Accept-Language": "ja"})
    body = r.json()
    titles = {chip["id"]: chip["title"] for cat in body["chips"].values() for chip in cat}
    assert "遅延ランキングTOP10" in titles.values()  # rank-delay-top


@pytest.mark.asyncio
async def test_build_schema_localizes_chip_titles_en(chips_app):
    app, agency_id, _ = chips_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/build-schema",
                        headers={"Accept-Language": "en"})
    body = r.json()
    titles = {chip["id"]: chip["title"] for cat in body["chips"].values() for chip in cat}
    assert "Top 10 by avg delay" in titles.values()


@pytest.mark.asyncio
async def test_popular_chips_empty_when_no_cache(chips_app):
    app, agency_id, _ = chips_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/popular-chips?limit=6")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_popular_chips_returns_top_by_hit_count(chips_app):
    app, agency_id, pool = chips_app
    # Seed two chip-equivalent cache rows with different hit counts.
    # Compute signature_hash for two known chips
    from datetime import date

    from pipeline.query.chip_catalog import CHIPS_BY_ID
    from pipeline.query.intent import canonicalize, signature_hash
    ctx = {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}
    h_topn  = signature_hash("top_n", canonicalize("top_n", CHIPS_BY_ID["rank-delay-top"].args, ctx))
    h_stops = signature_hash("describe_data", canonicalize("describe_data", CHIPS_BY_ID["meta-stops"].args, ctx))
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id) VALUES ($1,$2,$3::jsonb,0.99,$4,$5,$6)""",
            h_topn, "top_n", '{"metric": "avg_delay", "n": 10}', 8, "...", agency_id,
        )
        await conn.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id) VALUES ($1,$2,$3::jsonb,0.99,$4,$5,$6)""",
            h_stops, "describe_data", '{"kind": "stops"}', 3, "...", agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/popular-chips?limit=6")
    body = r.json()
    chip_ids = [chip["id"] for chip in body]
    assert "rank-delay-top" in chip_ids
    assert chip_ids.index("rank-delay-top") < chip_ids.index("meta-stops")  # higher hit_count first


@pytest.mark.asyncio
async def test_popular_chips_limit_clamped(chips_app):
    app, agency_id, _ = chips_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency_id}/ask/popular-chips?limit=999")
    assert len(r.json()) <= 12
