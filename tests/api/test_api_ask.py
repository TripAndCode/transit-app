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
        question,
        ctx,
        conn,
        agency_id,
        model="x",
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
        question,
        ctx,
        conn,
        agency_id,
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
    # Regression pin (item 8 / NEXT_TASK.md): a recognized pagination
    # follow-up must force a tool call rather than leave tool_choice="auto",
    # which live-observed a bare "次の50件" coming back with tool_call: None.
    assert captured["force_tool_call"] is True


@pytest.mark.asyncio
async def test_follow_up_phrasing_after_free_text_answer_does_not_force_tool(ask_client, monkeypatch):
    """Follow-up phrasing after a free-text (tool=None) turn must not force a tool call.

    is_follow_up()'s regex matches ordinary continuation phrasing ("他には")
    that can legitimately follow an out-of-scope refusal, not just a
    pagination continuation. Forcing tool_choice="required" there would
    remove the model's only correct move (decline again) and risk a
    hallucinated call instead (item 8 review finding).
    """
    client, agency_id = ask_client
    captured = {}

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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
async def test_unrelated_question_with_unrelated_history_gets_fresh_tool_call(ask_client, monkeypatch):
    """item 16 repro: an unrelated in-scope question with no follow-up
    phrasing, but with unrelated history from a prior ``top_n`` ranking
    turn attached, must still be able to surface a fresh tool call.

    Live-observed bug (2026-08-28): with an active conversation showing a
    route delay ranking table, a plain "停留所はいくつ？" ("how many stops
    are there?") — on topic but not continuation wording, so
    ``is_follow_up()`` correctly evaluates False and ``route_or_examples()``
    runs — came back as a prose non-answer ("the table doesn't include stop
    counts") instead of dispatching ``describe_data(kind=stops)``, because
    Stage 3 attached the full history and let the model reason from its
    (unrelated) table text instead of calling a tool.

    This exact question actually matches Stage 1's deterministic
    ``meta-stops`` rule (``pipeline/query/router.py``), so ``route_or_examples``
    is monkeypatched here to force a fall-through to Stage 3
    (``chat_with_tools``) exactly like the live repro, mirroring
    ``test_ask_writes_query_log_row``'s ``no_decision`` pattern in this same
    file. ``chat_with_tools`` is mocked here (as in the other tests in this
    file) to play the role of a correctly-behaving model — the real
    prompt-level fix that makes that behaviour likely is pinned separately in
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
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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


@pytest.mark.asyncio
async def test_ask_writes_query_log_row(ask_client, monkeypatch):
    client, agency_id = ask_client

    async def fake_chat(
        question,
        ctx,
        conn,
        agency_id,
        model=None,
        locale="ja",
        rag_examples=None,
        history=None,
        ch=None,
        force_tool_call=False,
        anon_quota=None,
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


# ---------------------------------------------------------------------------
# Anonymous Ask LLM-call daily quota (item 67)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_anon_quota():
    """Isolate the module-level in-memory anon-quota buckets between tests
    in this file — several tests below deliberately exhaust a low limit."""
    from api.middleware.ratelimit import reset_anon_quota_for_tests

    reset_anon_quota_for_tests()
    yield
    reset_anon_quota_for_tests()


@pytest.mark.asyncio
async def test_anon_session_cookie_issued_on_first_anonymous_request(ask_client, monkeypatch):
    """An anonymous POST /ask gets a signed httpOnly anon-session cookie on
    its first request, regardless of which stage answers it."""
    from api.middleware.ratelimit import ASK_ANON_SESSION_COOKIE_NAME

    client, agency_id = ask_client

    async def fake_chat(question, ctx, conn, agency_id, **kwargs):
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)

    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "何か珍しい質問ですABC123"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert ASK_ANON_SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie


@pytest.mark.asyncio
async def test_anon_session_cookie_reused_across_requests(ask_client, monkeypatch):
    """The anon-session cookie set on a first request is reused — same
    session_key threaded into chat_with_tools, no fresh Set-Cookie — on a
    second request from the same client."""
    client, agency_id = ask_client
    captured = []

    async def fake_chat(question, ctx, conn, agency_id, **kwargs):
        captured.append(kwargs.get("anon_quota"))
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp1 = await client.post(f"/api/{agency_id}/ask", json={"question": "質問その1"}, headers={"Origin": TEST_ORIGIN})
    assert resp1.status_code == 200
    assert "set-cookie" in resp1.headers

    resp2 = await client.post(f"/api/{agency_id}/ask", json={"question": "質問その2"}, headers={"Origin": TEST_ORIGIN})
    assert resp2.status_code == 200
    # httpx's AsyncClient persists + resends cookies across requests made on
    # the same client instance, so the cookie shouldn't need reissuing.
    assert "set-cookie" not in resp2.headers

    assert len(captured) == 2
    assert captured[0] is not None and captured[1] is not None
    assert captured[0].session_key == captured[1].session_key


@pytest.mark.asyncio
async def test_anon_quota_falls_back_sanely_with_no_cookie(ask_client, monkeypatch):
    """A caller that never sends the anon-session cookie back (e.g. cookies
    disabled) still gets served normally — a fresh session is minted for
    that single request rather than the call failing."""
    client, agency_id = ask_client
    captured = []

    async def fake_chat(question, ctx, conn, agency_id, **kwargs):
        captured.append(kwargs.get("anon_quota"))
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    # A fresh client (per-test `ask_client` fixture) has never received an
    # anon-session cookie — this simulates a client that drops cookies.
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "クッキーなしの質問"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200
    assert captured[0] is not None
    assert isinstance(captured[0].session_key, str) and captured[0].session_key


@pytest.mark.asyncio
async def test_logged_in_caller_bypasses_anon_quota(ask_client, monkeypatch):
    """A logged-in caller is never subject to the anon quota, and never
    gets an anon-session cookie issued, even across many calls."""
    from api.deps import get_current_user_optional
    from api.main import app
    from api.middleware.ratelimit import ASK_ANON_SESSION_COOKIE_NAME
    from api.security import User

    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")

    client, agency_id = ask_client
    fake_user = User(user_id=1, email="t@test", name="T", avatar_url=None, role="user", suspended_at=None)
    app.dependency_overrides[get_current_user_optional] = lambda: fake_user

    captured = []

    async def fake_chat(question, ctx, conn, agency_id, **kwargs):
        captured.append(kwargs.get("anon_quota"))
        return {"answer": "stub", "tool_call": None, "result": None, "success": True}

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.chat_with_tools", fake_chat)
    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    try:
        for _ in range(3):  # more than ASK_ANON_DAILY_LIMIT=1
            resp = await client.post(
                f"/api/{agency_id}/ask", json={"question": "質問"}, headers={"Origin": TEST_ORIGIN}
            )
            assert resp.status_code == 200
            assert ASK_ANON_SESSION_COOKIE_NAME not in resp.headers.get("set-cookie", "")
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)

    assert captured and all(c is None for c in captured)


@pytest.mark.asyncio
async def test_stage1_rule_hit_never_touches_anon_quota(ask_client, monkeypatch):
    """A deterministic rule-hit question never calls chat_with_tools — and
    therefore never consumes the anon LLM-call quota — even when asked more
    times than the configured daily limit."""
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    client, agency_id = ask_client

    async def must_not_be_called(*a, **kw):
        raise AssertionError("chat_with_tools should not be called on rule-hit")

    monkeypatch.setattr("api.routers.ask.chat_with_tools", must_not_be_called)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1 国道線')",
            agency_id,
        )
    await pool.close()

    for _ in range(3):  # more than ASK_ANON_DAILY_LIMIT=1
        resp = await client.post(
            f"/api/{agency_id}/ask",
            json={"question": "どんな路線がある？"},
            headers={"Origin": TEST_ORIGIN},
        )
        assert resp.status_code == 200
        assert resp.json().get("router_stage") == "rules"


@pytest.mark.asyncio
async def test_anonymous_caller_over_daily_limit_gets_429_with_code(ask_client, monkeypatch):
    """End-to-end: once an anonymous caller's daily Stage-3 LLM quota is
    exhausted, the NEXT call gets a distinct 429 + machine-readable code —
    not the generic slowapi RateLimitExceeded body, not a 200 degrade.

    Exercises the REAL chat_with_tools (not mocked), so the quota check
    inside it actually runs; only the LLM provider call itself is faked —
    mirrors tests/query/test_chat_null_args.py's ``_FakeClient`` pattern.
    """
    from types import SimpleNamespace

    from pipeline.query import chat as chat_module

    client, agency_id = ask_client
    monkeypatch.setenv("ASK_ANON_DAILY_LIMIT", "1")
    monkeypatch.setenv("ASK_ANON_IP_DAILY_LIMIT", "100")

    class _FakeClient:
        def chat_completions(self, **kwargs):
            func = SimpleNamespace(name="capabilities", arguments="{}")
            call = SimpleNamespace(function=func, id="call_1", type="function")
            return SimpleNamespace(content=None, tool_calls=[call]), None

    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient())

    async def no_decision(*a, **k):
        return (None, [])

    monkeypatch.setattr("api.routers.ask.route_or_examples", no_decision)

    resp1 = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "何ができますか？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp1.status_code == 200
    assert resp1.json()["tool_call"]["name"] == "capabilities"

    resp2 = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "他に何かできますか？"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp2.status_code == 429, f"expected 429, got {resp2.status_code}: {resp2.text[:200]}"
    data = resp2.json()
    assert data["code"] == "ask_anon_quota_exceeded"
