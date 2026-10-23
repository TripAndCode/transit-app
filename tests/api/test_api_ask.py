from datetime import date
from decimal import Decimal

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline.query.results import ToolResult
from tests.conftest import TEST_ORIGIN, _test_pool


@pytest.fixture
async def ask_app(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    # `ask()` declares ch=Depends(get_ch) alongside conn; every
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
            "agg_route_stats, agg_route_hour, "
            "agg_daily_trend, rag_chunks CASCADE"
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
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
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
async def test_ask_holds_no_pool_connection_while_the_llm_runs(ask_client, monkeypatch):
    """A Stage-3 question waits on the LLM for tens of seconds; pinning a pooled
    connection for that wait lets a handful of slow questions starve every
    other endpoint, so the pool must be fully idle while the model runs."""
    client, agency_id = ask_client
    from api.main import app

    pool = app.state.pool
    in_use_during_llm: list[int] = []

    async def mock_chat(question, ctx, conn, agency_id, **kwargs):
        # Real work around the LLM call still reaches the database.
        assert await conn.fetchval("SELECT 1") == 1
        in_use_during_llm.append(pool.get_size() - pool.get_idle_size())
        return {"answer": "ok", "tool_call": None, "result": None, "success": True}

    monkeypatch.setattr("api.routers.ask.chat_with_tools", mock_chat)
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "一番遅れている路線は？"},
        headers={"Origin": TEST_ORIGIN},
    )

    assert resp.status_code == 200
    assert in_use_during_llm == [0]


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
async def test_ask_sends_an_llm_answers_decimal_cells_as_numbers(ask_client, monkeypatch):
    """Tool rows carry NUMERIC as Decimal. /ask sends them as JSON numbers, as the
    conversation store does, so an anonymous thread's chart reads them."""
    client, agency_id = ask_client

    async def mock_chat(question, ctx, conn, agency_id, **kwargs):
        return {
            "answer": "a",
            "tool_call": {"name": "route_stop_patterns", "arguments": {}},
            "result": {
                "kind": "table",
                "summary": "s",
                "rows": [["S1", Decimal("3.40"), Decimal("10"), date(2026, 4, 1)]],
                "columns": ["stop", "avg_min", "samples", "date"],
                "series": [],
                "pairs": [],
            },
            "success": True,
        }

    monkeypatch.setattr("api.routers.ask.chat_with_tools", mock_chat)
    resp = await client.post(
        f"/api/{agency_id}/ask", json={"question": "どこで遅れる？"}, headers={"Origin": TEST_ORIGIN}
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["rows"] == [["S1", 3.4, 10, "2026-04-01"]]


@pytest.mark.asyncio
async def test_ask_sends_a_rule_answers_decimal_cells_as_numbers(ask_client, monkeypatch):
    """The rule and embedding stages build the same result, and send numbers too."""
    client, agency_id = ask_client

    async def fake_dispatch(tool, args, ctx, conn, agency_id, **kwargs):
        return ToolResult(kind="table", summary="s", rows=[["R1", Decimal("2.50")]], columns=["route", "avg_min"])

    monkeypatch.setattr("api.routers.ask.dispatch", fake_dispatch)
    resp = await client.post(
        f"/api/{agency_id}/ask", json={"question": "どんな路線がある？"}, headers={"Origin": TEST_ORIGIN}
    )
    assert resp.status_code == 200
    assert resp.json()["router_stage"] == "rules"
    assert resp.json()["result"]["rows"] == [["R1", 2.5]]


@pytest.mark.asyncio
async def test_ask_router_rule_hit_skips_llm(ask_client, monkeypatch):
    """A rule-match question should dispatch directly without calling chat_with_tools."""
    client, agency_id = ask_client

    async def must_not_be_called(*a, **kw):
        raise AssertionError("chat_with_tools should not be called on rule-hit")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", must_not_be_called)

    # Seed at least one route so describe_data(kind=routes) has data.

    pool = await _test_pool()
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
    """A rule-hit question routed to a ClickHouse-backed describe_data kind
    (date_range/overview/sample_counts) must return 200 with a graceful
    tool_error answer when ClickHouse is unavailable, not 503 the whole
    request.

    `ask_app`'s fixture sets `app.state.ch_client = None`, which `api.deps
    .get_ch` turns into the always-raising `_ClickHouseUnavailable`
    stand-in rather than a bare `None` — exercising the real dispatch path
    end-to-end, no mocking needed. Stage 1/2 must catch the stand-in's
    HTTPException(503) around `dispatch(...)`, as chat.py's Stage 3 does,
    rather than let it propagate up and 503 the endpoint.
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
    # The degraded answer must come from a fixed locale string, never
    # from the exception's raw text (the _ClickHouseUnavailable stand-in's
    # HTTPException.detail is "ClickHouse is unavailable") — that text must
    # never reach an unauthenticated client.
    assert "ClickHouse is unavailable" not in data["answer"]


@pytest.mark.asyncio
async def test_ask_stage1_dispatch_propagates_undefined_table_error(ask_client, monkeypatch):
    """Stage 1/2's dispatch(...) raising
    ``asyncpg.exceptions.UndefinedTableError`` (an ``agg_*`` table missing on a
    migration-lagged environment) must propagate out of the router body so
    FastAPI's registered ``aggregate_not_ready_handler`` (see api/main.py and
    api/aggregate_errors.py) catches it and returns the machine-readable
    ``{"code": "aggregate_not_ready"}`` 503 the frontend is built to react to.

    The degraded-answer handler around ``dispatch(...)`` in api/routers/ask.py
    must not catch it: answering a generic 200 ``tool_error`` instead would
    hide a missing table behind something that looks like an ordinary
    tool failure.
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
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["rag_examples"] = rag_examples
        captured["force_tool_call"] = force_tool_call
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
    # A non-follow-up novel question must NOT force a tool call — a genuine
    # out-of-scope refusal in free text is still a valid, deliberate answer.
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
async def test_follow_up_reroutes_to_llm_with_history(ask_client, monkeypatch):
    """A follow-up question skips the router and reaches chat_with_tools with history."""
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["history"] = history
        captured["force_tool_call"] = force_tool_call
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
    # A recognized pagination follow-up must force a tool call rather than
    # leave tool_choice="auto", under which a bare "次の50件" can come back
    # with tool_call: None.
    assert captured["force_tool_call"] is True


@pytest.mark.asyncio
async def test_follow_up_phrasing_after_free_text_answer_does_not_force_tool(ask_client, monkeypatch):
    """Follow-up phrasing after a free-text (tool=None) turn must not force a tool call.

    is_follow_up()'s regex matches ordinary continuation phrasing ("他には")
    that can legitimately follow an out-of-scope refusal, not just a
    pagination continuation. Forcing tool_choice="required" there would
    remove the model's only correct move (decline again) and risk a
    hallucinated call instead.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["force_tool_call"] = force_tool_call
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for follow-ups")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={
            "question": "他には？",
            "history": [{"question": "雨天時の比較は？", "tool": None, "args": None}],
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
async def test_follow_up_multiturn_history_only_looks_at_last_turn(ask_client, monkeypatch):
    """A 2+-turn history must gate force_tool_call on the LAST turn only.

    Turn 1 carried a real paginatable tool (describe_data); turn 2 is a
    free-text aside (tool=None, e.g. an out-of-scope refusal). The current,
    documented design only inspects history[-1] — so a bare continuation
    phrase here must NOT force a tool call, even though an earlier turn in
    the (capped-at-3) history did have one. This pins that as an explicit,
    tested decision rather than an untested gap.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["force_tool_call"] = force_tool_call
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for follow-ups")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={
            "question": "他には？",
            "history": [
                {"question": "停留所はいくつ？", "tool": "describe_data", "args": {"kind": "stops"}},
                {"question": "雨天時の比較は？", "tool": None, "args": None},
            ],
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
async def test_follow_up_non_paginatable_prior_tool_does_not_force_tool(ask_client, monkeypatch):
    """A prior tool with no continuation axis (no ``offset``) must not force a tool call.

    Only ``describe_data`` supports ``offset`` (see ``_TOOL_DEFAULTS`` in
    pipeline.query.intent). A bare "もっと" after a tool with no pagination
    concept (e.g. on_time_rate) has no valid re-invocation, so forcing
    tool_choice="required" would risk a nonsensical re-call instead of a
    legitimate prose answer.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["force_tool_call"] = force_tool_call
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for follow-ups")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={
            "question": "もっと",
            "history": [{"question": "定時率は？", "tool": "on_time_rate", "args": {}}],
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["もっと見せて", "もっと", "降順", "さらに", "その路線を降順で"])
async def test_followup_without_history_does_not_hallucinate(ask_client, monkeypatch, question):
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
        json={"question": question},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("router_stage") == "no_history"
    assert data["tool_call"] is None
    assert (
        data["answer"]
        == "前の検索結果が見つかりませんでした。まず質問してから「もっと」「次の50件」などで続けてください。"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["平日に絞り込んで遅延ワースト10", "定時率の低い順に並べ替えて"])
async def test_first_turn_follow_up_wording_a_rule_answers_reaches_the_router(ask_client, monkeypatch, question):
    """A Stage-1 rule answers these on their own, so a first turn using them
    is routed, not answered with the no-history prompt."""
    client, agency_id = ask_client
    routed: list[str] = []

    async def no_decision(q, *a, **k):
        routed.append(q)
        return (None, [])

    async def fake_chat(*a, **k):
        return {"answer": "ok", "tool_call": None, "result": None, "success": True}

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": question},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert routed == [question]
    assert resp.json()["router_stage"] == "llm"


@pytest.mark.asyncio
async def test_first_turn_follow_up_gets_the_no_history_prompt_when_the_router_is_off(ask_client, monkeypatch):
    """With the router off no Stage-1 rule will answer the question, so a
    first-turn continuation gets the no-history prompt even when a rule matches it."""
    client, agency_id = ask_client

    async def flags(key):
        return key != "ask_router_enabled"

    async def boom(*a, **k):
        raise AssertionError("neither the router nor the LLM may run for a no-history follow-up")

    monkeypatch.setattr("api.routers.ask.aflag", flags)
    monkeypatch.setattr("api.routers.ask.chat_with_tools", boom)
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "遅延ワースト10の続き"},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.json()["router_stage"] == "no_history"


_REFINEMENTS_WITH_A_SUBJECT = ["遅延の多い路線を降順で", "さらに定時率の低い路線", "別の路線と比較したい"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", _REFINEMENTS_WITH_A_SUBJECT)
async def test_first_turn_refinement_with_a_subject_of_its_own_reaches_the_router(ask_client, monkeypatch, question):
    """No rule answers these, but each names what to rank or compare, so a
    first turn is routed and then answered by the LLM, not given the
    no-history prompt."""
    client, agency_id = ask_client
    routed: list[str] = []
    captured: dict = {}

    async def no_decision(q, *a, **k):
        routed.append(q)
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat(captured))
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": question},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert routed == [question]
    assert resp.json()["router_stage"] == "llm"
    assert captured["history"] == []
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
async def test_first_turn_refinement_with_a_subject_reaches_the_llm_when_the_router_is_off(ask_client, monkeypatch):
    client, agency_id = ask_client
    captured: dict = {}

    async def flags(key):
        return key != "ask_router_enabled"

    async def boom(*a, **k):
        raise AssertionError("the router is off")

    monkeypatch.setattr("api.routers.ask.aflag", flags)
    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat(captured))
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "遅延の多い路線を降順で"},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.json()["router_stage"] == "llm"
    assert captured["history"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("question", _REFINEMENTS_WITH_A_SUBJECT)
async def test_refinement_with_a_subject_and_history_still_skips_the_router(ask_client, monkeypatch, question):
    """With history the refinement is read against the previous turn, so it
    goes to the LLM with that turn whether or not it names a subject."""
    client, agency_id = ask_client
    captured: dict = {}

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for a follow-up with history")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat(captured))
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": question, "history": [_ROUTE_TURN]},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.json()["router_stage"] == "llm"
    assert captured["history"] and captured["history"][0]["question"] == "22171の遅延"


@pytest.mark.asyncio
async def test_unrelated_question_with_unrelated_history_gets_fresh_tool_call(ask_client, monkeypatch):
    """An unrelated in-scope question with no follow-up phrasing, but with
    unrelated history from a prior ``top_n`` ranking turn attached, must
    still be able to surface a fresh tool call.

    The failure this guards: with an active conversation showing a route
    delay ranking table, a plain "停留所はいくつ？" ("how many stops are
    there?") — on topic but not continuation wording, so ``is_follow_up()``
    correctly evaluates False and ``route_or_examples()`` runs — can come
    back as a prose non-answer ("the table doesn't include stop counts")
    instead of dispatching ``describe_data(kind=stops)``, because Stage 3
    attaches the full history and the model can reason from its (unrelated)
    table text instead of calling a tool.

    This exact question actually matches Stage 1's deterministic
    ``meta-stops`` rule (``pipeline/query/router.py``), so ``route_or_examples``
    is monkeypatched here to force a fall-through to Stage 3
    (``chat_with_tools``), which is where the scenario above occurs, mirroring
    ``test_ask_writes_query_log_row``'s ``no_decision`` pattern in this same
    file. ``chat_with_tools`` is mocked here (as in the other tests in this
    file) to play the role of a correctly-behaving model — the real
    prompt-level guard that makes that behaviour likely is pinned separately in
    ``tests/query/test_chat_null_args.py``. This test pins the surrounding
    plumbing: not a recognized continuation (so ``force_tool_call`` stays
    False — tool_choice="auto" — see
    ``test_ask_router_fallthrough_passes_rag_examples``), history is still
    threaded through for possible anaphora resolution, and whatever tool
    call ``chat_with_tools`` returns reaches the client as a real tool call,
    not a prose non-answer.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["history"] = history
        captured["force_tool_call"] = force_tool_call
        return {
            "answer": "停留所は123件あります。",
            "tool_call": {"name": "describe_data", "arguments": {"kind": "stops"}},
            "result": {"kind": "kv", "summary": "", "rows": [], "columns": [], "series": [], "pairs": []},
            "success": True,
        }

    async def no_decision(*a, **kw):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={
            "question": "停留所はいくつ？",
            "history": [
                {"question": "遅延ランキングを見せて", "tool": "top_n", "args": {"metric": "avg_delay", "n": 10}}
            ],
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Not a recognized continuation of the prior tool: must not force a call.
    assert captured["force_tool_call"] is False
    # History is still threaded through to Stage 3 (it may help resolve
    # generic anaphora even outside is_follow_up()'s regex) ...
    assert captured["history"] and captured["history"][0]["question"] == "遅延ランキングを見せて"
    # ... but the response must be a real tool call, not a prose non-answer
    # anchored to that unrelated prior turn.
    assert data["router_stage"] == "llm"
    assert data["tool_call"] == {"name": "describe_data", "arguments": {"kind": "stops"}}


_ROUTE_TURN = {"question": "22171の遅延", "tool": "route_stats", "args": {"route_code": "22171"}}


def _recording_chat(captured: dict):
    async def fake_chat(question, ctx, conn, agency_id, **kwargs):
        captured["history"] = kwargs.get("history")
        captured["force_tool_call"] = kwargs.get("force_tool_call")
        return {"answer": "ok", "tool_call": None, "result": None, "success": True}

    return fake_chat


@pytest.mark.asyncio
async def test_a_reference_back_with_history_skips_the_router(ask_client, monkeypatch):
    """A question like "その路線は？" names no route of its own, so a stateless
    rule or embedding match would answer about whichever route its stored args
    name; with history attached it goes to the LLM, which sees the route it
    refers to."""
    client, agency_id = ask_client
    captured: dict = {}

    async def boom(*a, **k):
        raise AssertionError("router should be skipped for a reference back to the conversation")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat(captured))
    monkeypatch.setattr("api.routers.ask.route_or_examples", boom)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "その路線は？", "history": [_ROUTE_TURN]},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.json()["router_stage"] == "llm"
    assert captured["history"] and captured["history"][0]["question"] == "22171の遅延"
    # Not continuation wording, so the model keeps tool_choice="auto".
    assert captured["force_tool_call"] is False


@pytest.mark.asyncio
async def test_a_reference_back_without_history_still_reaches_the_router(ask_client, monkeypatch):
    client, agency_id = ask_client
    routed: list[str] = []

    async def no_decision(q, *a, **k):
        routed.append(q)
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat({}))
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "その路線は？"},  # no history
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert routed == ["その路線は？"]
    assert resp.json()["router_stage"] == "llm"


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["この1週間の推移", "定時率TOP10", "22171の遅延とその推移"])
async def test_a_self_contained_question_with_history_still_reaches_the_router(ask_client, monkeypatch, question):
    client, agency_id = ask_client
    routed: list[str] = []

    async def no_decision(q, *a, **k):
        routed.append(q)
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", _recording_chat({}))
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": question, "history": [_ROUTE_TURN]},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert routed == [question]


@pytest.mark.asyncio
async def test_ask_writes_query_log_row(ask_client, monkeypatch):
    client, agency_id = ask_client

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
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

    pool = await _test_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT question, router_stage FROM ask_query_log WHERE agency_id=$1 ORDER BY id DESC LIMIT 1",
            agency_id,
        )
    await pool.close()
    assert row is not None
    assert row["question"] == "なにか珍しい質問XYZ"
    assert row["router_stage"] == "llm"


@pytest.mark.asyncio
async def test_ask_logs_numeric_guard_verdict(ask_client, monkeypatch):
    """chat_with_tools' guard verdict reaches ask_query_log, not just its own tests."""
    client, agency_id = ask_client

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        return {
            "answer": "ok",
            "tool_call": None,
            "result": None,
            "success": True,
            "numeric_guard_triggered": True,
        }

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "平均遅延はどれくらい？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200

    pool = await _test_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT numeric_guard_triggered FROM ask_query_log WHERE agency_id=$1 ORDER BY id DESC LIMIT 1",
            agency_id,
        )
    await pool.close()
    assert row is not None
    assert row["numeric_guard_triggered"] is True


@pytest.mark.asyncio
async def test_stage1_rule_hit_never_calls_chat_with_tools(ask_client, monkeypatch):
    """A deterministic rule-hit question is answered by the router alone, no
    matter how often it is asked — it never reaches the LLM orchestrator."""
    client, agency_id = ask_client

    async def must_not_be_called(*a, **kw):
        raise AssertionError("chat_with_tools should not be called on rule-hit")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", must_not_be_called)

    pool = await _test_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1 国道線')",
            agency_id,
        )
    await pool.close()

    for _ in range(3):  # repetition must not push it off the rules path
        resp = await client.post(
            f"/api/{agency_id}/ask",
            json={"question": "どんな路線がある？"},
            headers={"Origin": TEST_ORIGIN},
        )
        assert resp.status_code == 200
        assert resp.json().get("router_stage") == "rules"


@pytest.mark.asyncio
async def test_anonymous_caller_never_reaches_llm(ask_client, monkeypatch):
    """An anonymous caller never has a users.llm_approved row, so every
    Stage-3 question gets the honest "not approved" degrade (200, no
    tool_call) on the very first call, and never the provider, no matter
    how many times they ask.

    Exercises the REAL chat_with_tools (not mocked); only the LLM provider
    call itself is faked, mirroring tests/query/test_chat_null_args.py's
    ``_FakeClient`` pattern, so a call to it here would be a real failure.
    """
    from types import SimpleNamespace

    from pipeline.query import chat as chat_module

    client, agency_id = ask_client
    calls = {"n": 0}

    class _FakeClient:
        def chat_completions(self, **kwargs):
            calls["n"] += 1
            func = SimpleNamespace(name="capabilities", arguments="{}")
            call = SimpleNamespace(function=func, id="call_1", type="function")
            return SimpleNamespace(content=None, tool_calls=[call]), None

    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient())

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    for question in ("何ができますか？", "他に何かできますか？"):
        resp = await client.post(
            f"/api/{agency_id}/ask",
            json={"question": question},
            headers={"Origin": TEST_ORIGIN},
        )
        assert resp.status_code == 200
        assert resp.json()["tool_call"] is None

    assert calls["n"] == 0, "the LLM provider must never be reached by an unapproved caller"


@pytest.mark.asyncio
async def test_ask_forwards_panel_ctx_to_chat_with_tools(ask_client, monkeypatch):
    """The Copilot side panel's optional panel_ctx hint reaches chat_with_tools
    untouched — it is a system-prompt grounding hint only, never consulted by
    the rules/embedding routing stage above it.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["panel_ctx"] = panel_ctx
        return {"answer": "ok", "tool_call": None, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "遅延状況は？", "panel_ctx": {"tab": "overview"}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["panel_ctx"] == {"tab": "overview"}


@pytest.mark.asyncio
async def test_ask_omits_panel_ctx_by_default(ask_client, monkeypatch):
    """A request with no panel_ctx field forwards None — the pre-existing,
    Ask-tab (non-Copilot) call shape is unaffected.
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        panel_ctx=None,
        user_id=None,
        llm_approved=True,
    ):
        captured["panel_ctx"] = panel_ctx
        return {"answer": "ok", "tool_call": None, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "遅延状況は？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured["panel_ctx"] is None


@pytest.mark.asyncio
async def test_ask_rejects_unknown_panel_ctx_tab(ask_client):
    """panel_ctx.tab is a closed enum (the frontend's known tab routes) — an
    arbitrary/oversized string must be rejected before it can ever reach the
    system prompt chat_with_tools builds from this hint.
    """
    client, agency_id = ask_client

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "遅延状況は？", "panel_ctx": {"tab": "not-a-real-tab"}},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signed_in_unapproved_caller_never_reaches_llm(ask_client, monkeypatch):
    """A signed-in caller whose ``users.llm_approved`` is False gets the same
    honest degrade an anonymous caller does -- 200, no tool_call, provider
    never called.

    Covers the router's own ``llm_approved=user.llm_approved if user is not
    None else False`` wiring, which no other test exercises: the anonymous
    case takes the ``else False`` branch, and every other signed-in test
    passes ``llm_approved=True``. Inverting or dropping that ternary would
    silently let unapproved signed-in users reach the LLM, and only this
    test would catch it. Exercises the REAL chat_with_tools with just the
    provider faked, mirroring test_anonymous_caller_never_reaches_llm.
    """
    from types import SimpleNamespace

    from api.deps import get_current_user_optional
    from api.main import app
    from api.security import User
    from pipeline.query import chat as chat_module

    client, agency_id = ask_client
    unapproved = User(
        user_id=1, email="t@test", name="T", avatar_url=None, role="user", suspended_at=None, llm_approved=False
    )
    app.dependency_overrides[get_current_user_optional] = lambda: unapproved

    calls = {"n": 0}

    class _FakeClient:
        def chat_completions(self, **kwargs):
            calls["n"] += 1
            func = SimpleNamespace(name="capabilities", arguments="{}")
            call = SimpleNamespace(function=func, id="call_1", type="function")
            return SimpleNamespace(content=None, tool_calls=[call]), None

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    try:
        resp = await client.post(
            f"/api/{agency_id}/ask",
            json={"question": "何ができますか？"},
            headers={"Origin": TEST_ORIGIN},
        )
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)

    assert resp.status_code == 200
    assert resp.json()["tool_call"] is None
    assert calls["n"] == 0, "the LLM provider must never be reached by an unapproved signed-in caller"
