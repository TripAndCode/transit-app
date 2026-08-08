import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def ask_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    # `ask()` now declares ch=Depends(get_ch) alongside conn (Task 8); every
    # test in this file mocks chat_with_tools/dispatch so the real client is
    # never touched, but FastAPI still resolves the dependency, so something
    # must be present at app.state.ch_client — None is fine here.
    app.state.ch_client = None
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
async def ask_client(ask_app):
    app, agency_id = ask_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.mark.asyncio
async def test_ask_endpoint_returns_answer(ask_client, monkeypatch):
    """v2 ask uses tool-use; mock chat_with_tools so the test is offline."""
    client, agency_id = ask_client

    async def mock_chat(
        question, ctx, conn, agency_id, model="x", locale="ja", rag_examples=None, history=None, ch=None
    ):
        return {
            "answer": "テスト回答",
            "tool_call": {"name": "top_n", "arguments": {"metric": "avg_delay", "n": 5}},
            "result": {
                "kind": "table",
                "summary": "テスト",
                "rows": [],
                "columns": ["route_code", "service_type"],
                "series": [],
                "pairs": [],
            },
            "success": True,
        }

    # Patch the symbol in the module that imports it (api.routers.ask)
    monkeypatch.setattr("api.routers.ask.chat_with_tools", mock_chat)
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "一番遅れている路線は？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "テスト回答"
    assert data["tool_call"]["name"] == "top_n"
    assert data["result"]["kind"] == "table"
    assert data["ctx"]["from"]
    assert data["ctx"]["to"]


@pytest.mark.asyncio
async def test_ask_endpoint_unknown_agency(ask_client):
    client, _ = ask_client
    resp = await client.post(
        "/api/99999/ask",
        json={"question": "test"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ask_rejects_cross_origin(ask_client, monkeypatch):
    """Cross-origin POST to /ask returns 403 even before reaching the LLM."""
    client, agency_id = ask_client

    # If csrf_guard somehow misses, chat_with_tools would be hit.
    # Patch it to a sentinel so a 200 with this answer indicates the guard
    # let the request through (= bug).
    async def must_not_be_called(*args, **kwargs):
        _ = kwargs.get("locale", "ja")
        return {
            "answer": "csrf_guard FAILED — request reached chat_with_tools",
            "tool_call": None,
            "result": None,
        }

    monkeypatch.setattr("api.routers.ask.chat_with_tools", must_not_be_called)
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "テスト"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text[:200]}"


@pytest.mark.asyncio
async def test_ask_router_rule_hit_skips_llm(ask_client, monkeypatch):
    """A rule-match question should dispatch directly without calling chat_with_tools."""
    client, agency_id = ask_client

    async def must_not_be_called(*a, **kw):
        raise AssertionError("chat_with_tools should not be called on rule-hit")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", must_not_be_called)

    # Seed at least one route so describe_data(kind=routes) has data.
    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1 国道線')",
            agency_id,
        )
    await pool.close()

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "どんな路線がある？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_call"]["name"] == "describe_data"
    assert data["tool_call"]["arguments"]["kind"] == "routes"
    assert data.get("router_stage") == "rules"


@pytest.mark.asyncio
async def test_ask_stage1_dispatch_degrades_on_clickhouse_unavailable(ask_client):
    """Fix B follow-up regression: a rule-hit question routed to a
    ClickHouse-backed describe_data kind (date_range/overview/sample_counts)
    must return 200 with a graceful tool_error answer when ClickHouse is
    unavailable, not 503 the whole request.

    `ask_app`'s fixture sets `app.state.ch_client = None`, which `api.deps
    .get_ch` now turns into the always-raising `_ClickHouseUnavailable`
    stand-in (Fix A) rather than a bare `None` — exercising the real
    dispatch path end-to-end, no mocking needed. Before this fix, Stage 1/2
    had no try/except around `dispatch(...)` (unlike chat.py's Stage 3,
    which already degrades this way), so the stand-in's HTTPException(503)
    propagated all the way up and 503'd the endpoint.
    """
    client, agency_id = ask_client
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "いつからのデータ?"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_call"]["name"] == "describe_data"
    assert data["tool_call"]["arguments"]["kind"] == "date_range"
    assert data.get("router_stage") == "rules"
    assert data["result"] is None
    assert "describe_data" in data["answer"]
    # Fix 8f: the degraded answer must come from a fixed locale string, never
    # from the exception's raw text (the _ClickHouseUnavailable stand-in's
    # HTTPException.detail is "ClickHouse is unavailable") — that text must
    # never reach an unauthenticated client.
    assert "ClickHouse is unavailable" not in data["answer"]


@pytest.mark.asyncio
async def test_ask_stage1_dispatch_propagates_undefined_table_error(ask_client, monkeypatch):
    """Fix 8f regression: Stage 1/2's dispatch(...) raising
    ``asyncpg.exceptions.UndefinedTableError`` (an ``agg_*`` table missing on a
    migration-lagged environment) must propagate out of the router body so
    FastAPI's registered ``aggregate_not_ready_handler`` (see api/main.py and
    api/aggregate_errors.py) catches it and returns the machine-readable
    ``{"code": "aggregate_not_ready"}`` 503 the frontend is built to react to.

    Before this fix, the blanket ``except Exception`` around ``dispatch(...)``
    in api/routers/ask.py swallowed this and answered a generic 200
    ``tool_error`` instead — exactly the regression this fix closes.
    """
    client, agency_id = ask_client

    async def raise_undefined_table(*args, **kwargs):
        raise asyncpg.exceptions.UndefinedTableError('relation "agg_route_daily_dist" does not exist')

    monkeypatch.setattr("api.routers.ask.dispatch", raise_undefined_table)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "いつからのデータ?"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 503, f"expected 503 aggregate_not_ready, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert data["code"] == "aggregate_not_ready"
    # The internal relation name is server-log-only, never client-visible.
    assert "agg_route_daily_dist" not in data["detail"]


@pytest.mark.asyncio
async def test_ask_router_fallthrough_passes_rag_examples(ask_client, monkeypatch):
    """Novel question → router returns None → chat_with_tools called with rag_examples kwarg."""
    client, agency_id = ask_client

    captured = {}

    async def fake_chat(
        question, ctx, conn, agency_id, model=None, locale="ja", rag_examples=None, history=None, ch=None
    ):
        captured["rag_examples"] = rag_examples
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "雨の日とそうでない日を比べたいです"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    # rag_examples is a list (possibly empty if rag_chunks is empty for this agency).
    assert isinstance(captured["rag_examples"], list)


@pytest.mark.asyncio
async def test_follow_up_reroutes_to_llm_with_history(ask_client, monkeypatch):
    """A follow-up question skips the router and reaches chat_with_tools with history."""
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question, ctx, conn, agency_id, model=None, locale="ja", rag_examples=None, history=None, ch=None
    ):
        captured["history"] = history
        return {
            "answer": "stub",
            "tool_call": {"name": "describe_data", "arguments": {"kind": "stops", "offset": 50}},
            "result": None,
            "success": True,
        }

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for follow-ups")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={
            "question": "次の50件",
            "history": [{"question": "停留所はいくつ？", "tool": "describe_data", "args": {"kind": "stops"}}],
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["history"] and captured["history"][0]["question"] == "停留所はいくつ？"
    assert resp.json().get("router_stage") == "llm"


@pytest.mark.asyncio
async def test_followup_without_history_does_not_hallucinate(ask_client, monkeypatch):
    """A follow-up phrasing with no history returns a gentle prompt, not an LLM-invented page."""
    client, agency_id = ask_client

    async def boom_chat(*a, **k):
        raise AssertionError("chat_with_tools must NOT be called for a no-history follow-up")

    async def boom_router(*a, **k):
        raise AssertionError("router must NOT be called for a no-history follow-up")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", boom_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom_router)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "もっと見せて"},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("router_stage") == "no_history"
    assert data["tool_call"] is None


@pytest.mark.asyncio
async def test_ask_writes_query_log_row(ask_client, monkeypatch):
    client, agency_id = ask_client

    async def fake_chat(
        question, ctx, conn, agency_id, model=None, locale="ja", rag_examples=None, history=None, ch=None
    ):
        return {"answer": "ok", "tool_call": {"name": "top_n", "arguments": {}}, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "なにか珍しい質問XYZ"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT question, router_stage FROM ask_query_log WHERE agency_id=$1 ORDER BY id DESC LIMIT 1",
            agency_id,
        )
    await pool.close()
    assert row is not None
    assert row["question"] == "なにか珍しい質問XYZ"
    assert row["router_stage"] == "llm"
