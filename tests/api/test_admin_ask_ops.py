"""Tests for GET /api/admin/ask/* — the admin Ask-ops query log and funnel."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import _test_pool
from tests.fixtures.users import seed_session


@pytest.fixture
async def ask_ops_client(apply_schema, monkeypatch):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, sessions, users, ask_query_log, ask_intent_cache, rag_chunks, admin_audit CASCADE"
        )
        admin_sid, admin_uid, _ = await seed_session(conn, role="admin")
        agency_id = (
            await conn.fetchrow(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('AskOpsAgency', 'http://x') RETURNING agency_id"
            )
        )["agency_id"]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, admin_sid, admin_uid, agency_id, pool
    await pool.close()


async def _insert_log(
    pool,
    agency_id,
    *,
    router_stage="llm",
    tool="top_n",
    success=True,
    signature_hash=None,
    question="遅延が大きい路線は?",
):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """
            INSERT INTO ask_query_log (agency_id, question, router_stage, tool, success, signature_hash)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            agency_id,
            question,
            router_stage,
            tool,
            success,
            signature_hash,
        )
    return row["id"]


@pytest.mark.asyncio
async def test_queries_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/queries")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_queries_returns_derived_route_and_status(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="embedding", success=False)
    await _insert_log(pool, agency_id, router_stage="llm", success=True, signature_hash="aaaaaaaaaaaaaaaa")

    resp = await c.get("/api/admin/ask/queries", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 3
    by_route = {r["route"]: r for r in body["rows"]}
    assert by_route["rules"]["status"] == "ok"
    assert by_route["nn"]["status"] == "error"
    assert "promotable" not in body["rows"][0]
    assert "user" not in body["rows"][0]
    assert "latency_ms" not in body["rows"][0]


@pytest.mark.asyncio
async def test_queries_filters_by_route_and_status(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="llm", success=False, signature_hash="bbbbbbbbbbbbbbbb")

    resp = await c.get("/api/admin/ask/queries", params={"route": "rag"}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["route"] == "rag"

    resp = await c.get("/api/admin/ask/queries", params={"status": "error"}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_queries_rejects_unknown_route_and_status(ask_ops_client):
    c, sid, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/queries", params={"route": "bogus"}, cookies={"sid": sid})
    assert resp.status_code == 400
    resp = await c.get("/api/admin/ask/queries", params={"status": "bogus"}, cookies={"sid": sid})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_queries_cursor_pagination(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    for _ in range(3):
        await _insert_log(pool, agency_id)

    resp = await c.get("/api/admin/ask/queries", params={"limit": 2}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["next_cursor"] is not None

    resp2 = await c.get(
        "/api/admin/ask/queries", params={"limit": 2, "cursor": body["next_cursor"]}, cookies={"sid": sid}
    )
    body2 = resp2.json()
    assert len(body2["rows"]) == 1
    assert body2["next_cursor"] is None
    seen_ids = {r["id"] for r in body["rows"]} | {r["id"] for r in body2["rows"]}
    assert len(seen_ids) == 3


@pytest.mark.asyncio
async def test_funnel_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/funnel")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_funnel_aggregates_by_route_and_omits_providers(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="llm", success=False, signature_hash="cccccccccccccccc")

    resp = await c.get("/api/admin/ask/funnel", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"] is None
    by_route = {r["route"]: r for r in body["by_route"]}
    assert by_route["rules"]["count"] == 2
    assert by_route["rules"]["success_count"] == 2
    assert by_route["rag"]["count"] == 1
    assert by_route["rag"]["success_count"] == 0
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_funnel_defaults_to_the_last_30_days_excluding_older_rows(ask_ops_client):
    """With neither `from` nor `to` given, a row well outside the trailing
    30-day window must not be counted -- an unbounded funnel only gets more
    expensive as ask_query_log grows."""
    c, sid, _uid, agency_id, pool = ask_ops_client
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ask_query_log (agency_id, question, router_stage, success, created_at)
            VALUES ($1, 'old question', 'rules', true, now() - INTERVAL '90 days')
            """,
            agency_id,
        )
    await _insert_log(pool, agency_id, router_stage="rules", success=True)

    resp = await c.get("/api/admin/ask/funnel", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Passing an explicit range wide enough to cover the old row includes it.
    wide_from = (datetime.now(timezone.utc) - timedelta(days=120)).date().isoformat()
    resp = await c.get("/api/admin/ask/funnel", params={"from": wide_from}, cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_both_date_bounds_are_jst_days_whatever_the_process_zone(ask_ops_client, monkeypatch):
    """A row at 03:00 JST on 1 October falls inside from=to=2026-10-01 even
    when the API process runs in UTC, as a container does."""
    c, sid, _uid, agency_id, _pool = ask_ops_client
    from api.main import app

    app.state.pool = jst_pool = await _test_pool()
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        async with jst_pool.acquire() as conn:
            jst = timezone(timedelta(hours=9))
            for at in (
                datetime(2026, 10, 1, 3, 0, tzinfo=jst),
                datetime(2026, 10, 1, 23, 30, tzinfo=jst),
                datetime(2026, 9, 30, 23, 30, tzinfo=jst),
                datetime(2026, 10, 2, 0, 30, tzinfo=jst),
            ):
                await conn.execute(
                    "INSERT INTO ask_query_log (agency_id, question, router_stage, success, created_at)"
                    " VALUES ($1, 'q', 'rules', true, $2)",
                    agency_id,
                    at,
                )
        day = {"from": "2026-10-01", "to": "2026-10-01"}
        funnel = await c.get("/api/admin/ask/funnel", params=day, cookies={"sid": sid})
        assert funnel.json()["total"] == 2
        log = await c.get("/api/admin/ask/queries", params=day, cookies={"sid": sid})
        assert len(log.json()["rows"]) == 2
    finally:
        monkeypatch.undo()
        time.tzset()
        await jst_pool.close()


@pytest.mark.asyncio
async def test_the_api_session_settings_survive_a_connection_going_back_to_the_pool(apply_schema):
    """The pool runs RESET ALL whenever it takes a connection back, which
    undoes a SET. Every later request on that connection still has to see
    the JST calendar and the statement cap."""
    pool = await _test_pool(max_size=1)
    try:
        for _ in range(2):
            async with pool.acquire() as conn:
                assert await conn.fetchval("SHOW TIME ZONE") == "Asia/Tokyo"
                assert await conn.fetchval("SHOW statement_timeout") == "30s"
    finally:
        await pool.close()


async def _insert_notebook_log(pool, agency_id, stage, tool, success=True, latency_ms=100, age_days=0):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ask_query_log (agency_id, question, router_stage, tool, success, latency_ms, created_at) "
            "VALUES ($1, '', $2, $3, $4, $5, now() - $6 * INTERVAL '1 day')",
            agency_id,
            stage,
            tool,
            success,
            latency_ms,
            age_days,
        )


@pytest.mark.asyncio
async def test_tools_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/tools")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_tools_counts_notebook_paths_per_tool_for_the_last_30_days(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_notebook_log(pool, agency_id, "card_signed_in", "top_n", latency_ms=100)
    await _insert_notebook_log(pool, agency_id, "card_signed_in", "top_n", success=False, latency_ms=300)
    await _insert_notebook_log(pool, agency_id, "card_signed_out", "top_n", latency_ms=200)
    await _insert_notebook_log(pool, agency_id, "followup", "top_n", latency_ms=None)
    await _insert_notebook_log(pool, agency_id, "card_signed_out", "time_series")
    await _insert_notebook_log(pool, agency_id, "card_signed_out", None, success=False)
    # Outside the window, and not a notebook path: neither is counted.
    await _insert_notebook_log(pool, agency_id, "card_signed_in", "top_n", age_days=45)
    await _insert_log(pool, agency_id, router_stage="rules", tool="top_n")

    resp = await c.get("/api/admin/ask/tools", cookies={"sid": sid})
    assert resp.status_code == 200
    by_tool = {t["tool"]: t for t in resp.json()["tools"]}
    assert by_tool["top_n"] == {
        "tool": "top_n",
        "signed_in": 2,
        "signed_out": 1,
        "followups": 1,
        "failed": 1,
        "avg_latency_ms": 200,
    }
    assert by_tool["time_series"]["signed_out"] == 1
    assert by_tool[None]["failed"] == 1
    assert resp.json()["tools"][0]["tool"] == "top_n"


@pytest.mark.asyncio
async def test_funnel_leaves_out_notebook_dispatches(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules")
    await _insert_notebook_log(pool, agency_id, "card_signed_in", "top_n")
    await _insert_notebook_log(pool, agency_id, "followup", "top_n")
    body = (await c.get("/api/admin/ask/funnel", cookies={"sid": sid})).json()
    assert body["total"] == 1
    assert [r["route"] for r in body["by_route"]] == ["rules", "nn", "rag", "no_history"]


@pytest.mark.asyncio
async def test_queries_filter_by_notebook_route(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules")
    await _insert_notebook_log(pool, agency_id, "card_signed_out", "top_n")
    rows = (await c.get("/api/admin/ask/queries", params={"route": "card_signed_out"}, cookies={"sid": sid})).json()[
        "rows"
    ]
    assert [(r["route"], r["tool"]) for r in rows] == [("card_signed_out", "top_n")]
