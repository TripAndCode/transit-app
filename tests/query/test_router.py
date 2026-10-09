import json as _json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from pipeline.query.router import (
    _match_rules,
    is_follow_up,
    retrieve_examples,
    route_question,
    set_golden_set_path,
)
from tests.conftest import _test_pool


class _FakeEmbedder:
    available = True

    def embed(self, text: str, *, mode: str) -> list[float]:
        # Deterministic small vectors keyed on text content.
        if "中央大橋" in text:
            return [1.0] + [0.0] * 383
        if "国道" in text:
            return [0.0, 1.0] + [0.0] * 382
        return [0.5, 0.5] + [0.0] * 382


@pytest.fixture
def fake_embedder(monkeypatch):
    e = _FakeEmbedder()
    from pipeline.query import router

    monkeypatch.setattr(router, "_get_embedder", lambda: e)
    yield e


@pytest.fixture
def golden_jsonl(tmp_path, monkeypatch):
    p = tmp_path / "golden.jsonl"
    p.write_text(
        "\n".join(
            [
                _json.dumps(
                    {
                        "id": "g-1",
                        "question": "中央大橋線の遅延",
                        "expected_tool": "route_stats",
                        "expected_args": {"route": "12211"},
                    }
                ),
                _json.dumps(
                    {"id": "g-2", "question": "国道線の傾向", "expected_tool": "time_series", "expected_args": {}}
                ),
            ]
        )
    )
    set_golden_set_path(p)
    yield p
    set_golden_set_path(None)


@pytest_asyncio.fixture
async def conn_with_embedded_chunks(apply_schema):
    pool = await _test_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        # Insert chunks whose embeddings match _FakeEmbedder's responses.
        v_a = "[" + ",".join(["1.0"] + ["0.0"] * 383) + "]"
        v_b = "[" + ",".join(["0.0", "1.0"] + ["0.0"] * 382) + "]"
        await conn.executemany(
            "INSERT INTO rag_chunks (chunk_id, agency_id, content, embedding, content_hash) "
            "VALUES ($1, $2, $3, $4::vector, $5)",
            [
                ("g-1", agency_id, "中央大橋線の遅延", v_a, "h-a"),
                ("g-2", agency_id, "国道線の傾向", v_b, "h-b"),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_route_question_rule_hit(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    """Rule path beats Stage 2 even when chunks exist."""
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        decision = await route_question("どんな路線がある？", conn, agency_id)
    assert decision is not None
    assert decision.stage == "rules"
    assert decision.tool == "describe_data"
    assert decision.args["kind"] == "routes"


@pytest.mark.asyncio
async def test_route_question_embedding_hit(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    """No rule matches → embedding finds g-1 with distance ~0 → dispatch."""
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        decision = await route_question("中央大橋線の遅延", conn, agency_id)
    assert decision is not None
    assert decision.stage == "embedding"
    assert decision.tool == "route_stats"
    assert decision.args == {"route": "12211"}


@pytest.mark.asyncio
async def test_route_question_no_match(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    """Distant query (no rule hit) → distance > threshold → returns None."""
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        # Avoid OOS-guard keywords (e.g. 天気) so this exercises Stage 2 only.
        decision = await route_question("なんとなく気になる", conn, agency_id)
    assert decision is None


@pytest.mark.asyncio
async def test_route_question_rejects_above_threshold(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    # _FakeEmbedder returns [0.5,0.5,0,...] for unknown text → distance ~0.29 from both axis chunks → no dispatch
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        d = await route_question("全然関係ない質問", conn, agency_id)
    assert d is None


@pytest.mark.parametrize(
    "question,expected_tool,expected_kind",
    [
        ("どんな路線がデータにあるの？", "describe_data", "routes"),
        ("路線一覧を見せて", "describe_data", "routes"),
        ("いつからのデータ？", "describe_data", "date_range"),
        ("最新のデータはいつ？", "describe_data", "date_range"),
        ("何件くらいの観測がある？", "describe_data", "date_range"),
        ("停留所はいくつ？", "describe_data", "stops"),
        ("何社の事業者？", "describe_data", "agencies"),
        ("全体の概要を", "describe_data", "overview"),
        ("計算できる指標は？", "describe_data", "metrics"),
        ("サンプル数の多い系統", "describe_data", "sample_counts"),
    ],
)
def test_rule_meta_dispatch(question, expected_tool, expected_kind):
    decision = _match_rules(question)
    assert decision is not None
    assert decision.tool == expected_tool
    assert decision.args.get("kind") == expected_kind
    assert decision.stage == "rules"


@pytest.mark.parametrize(
    "question",
    [
        "雨の日の遅延は？",
        "なぜ最近遅れているの？",
        "全国平均と比べて？",
    ],
)
def test_rule_no_match(question):
    """Questions outside the rule set return None — fall through to Stage 2."""
    assert _match_rules(question) is None


def test_oos_guard_routes_to_capabilities():
    for q in ["今日の天気は？", "運賃はいくら？", "事故情報を教えて"]:
        d = _match_rules(q)
        assert d is not None and d.tool == "capabilities", q


def test_metrics_rule_does_not_overfire_on_definition():
    # A definition question should NOT hit meta-metrics
    d = _match_rules("定時率という指標の意味を教えて")
    # acceptable: either None (falls through) or capabilities — but NOT describe_data/metrics
    assert d is None or d.tool != "describe_data"


def test_rule_5min_not_shadowed_by_worst():
    d = _match_rules("5分以上の遅れが多い系統TOP10")
    assert d is not None
    assert d.tool == "top_n"
    assert d.args["metric"] == "worst_5min"


def test_rule_honors_captured_n():
    d = _match_rules("遅延ワースト3")
    assert d is not None
    assert d.tool == "top_n"
    assert d.args["n"] == 3


def test_rule_default_n_when_no_digit():
    d = _match_rules("遅延ワースト")
    assert d is not None
    assert d.args["n"] == 10


def test_rule_decision_records_pattern_name():
    decision = _match_rules("どんな路線がある？")
    assert decision is not None
    assert decision.matched_pattern  # non-empty rule name


def test_load_golden_skips_malformed_lines(tmp_path):
    from pipeline.query.router import _load_golden, set_golden_set_path

    p = tmp_path / "golden.jsonl"
    p.write_text(
        "\n".join(
            [
                _json.dumps({"id": "ok-1", "expected_tool": "top_n", "expected_args": {}}),
                "{ this is not valid json",
                _json.dumps({"id": "ok-2", "expected_tool": "describe_data", "expected_args": {}}),
            ]
        )
    )
    set_golden_set_path(p)
    try:
        mapping = _load_golden()
        assert set(mapping.keys()) == {"ok-1", "ok-2"}
    finally:
        set_golden_set_path(None)


def test_all_rules_map_to_known_tools():
    """Every rule's `tool` must exist in the dispatcher's _HANDLERS."""
    from pipeline.query.router import _RULES
    from pipeline.query.tools import _HANDLERS

    known = set(_HANDLERS.keys())
    bad = [r.name for r in _RULES if r.tool not in known]
    assert not bad, f"rules with unknown tool name: {bad}"


@pytest.mark.asyncio
async def test_retrieve_examples_returns_top_k_with_tool_args(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    """Even when route_question returns None, retrieve_examples should give top-3."""
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        # Non-OOS, non-rule phrase so we exercise the Stage 2 fall-through.
        matches = await retrieve_examples("なんとなく気になる", conn, agency_id, k=3)
    # Two chunks in fixture, so we get 2 (capped by available data).
    assert len(matches) == 2
    # Tool/args populated from the golden_set dict.
    tools = {m.tool for m in matches}
    assert tools == {"route_stats", "time_series"}
    assert all(m.args is not None for m in matches)


@pytest.mark.asyncio
async def test_route_or_examples_single_path(conn_with_embedded_chunks, fake_embedder, golden_jsonl):
    from pipeline.query.router import route_or_examples

    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        # rule hit: decision, no examples
        dec, ex = await route_or_examples("どんな路線がある？", conn, agency_id)
        assert dec is not None and dec.stage == "rules" and ex == []
        # embedding hit
        dec, ex = await route_or_examples("中央大橋線の遅延", conn, agency_id)
        assert dec is not None and dec.stage == "embedding"
        # miss → examples
        dec, ex = await route_or_examples("全然関係ない質問", conn, agency_id)
        assert dec is None and isinstance(ex, list)


@pytest.mark.asyncio
async def test_retrieve_examples_empty_when_embedder_unavailable(conn_with_embedded_chunks, monkeypatch, golden_jsonl):
    pool, agency_id = conn_with_embedded_chunks

    class _Down:
        available = False

        def embed(self, *a, **kw):
            raise RuntimeError("down")

    from pipeline.query import router

    monkeypatch.setattr(router, "_get_embedder", lambda: _Down())
    async with pool.acquire() as conn:
        matches = await retrieve_examples("anything", conn, agency_id, k=3)
    assert matches == []


@pytest.mark.asyncio
async def test_margin_guard_ignores_same_tool_runnerup(monkeypatch, tmp_path):
    """Two close same-tool matches must dispatch; close different-tool matches fall through."""
    import json as _json

    from pipeline.query import router as _router
    from pipeline.query.rag_index import Match

    p = tmp_path / "g.jsonl"
    p.write_text(
        "\n".join(
            [
                _json.dumps(
                    {"id": "a", "question": "x", "expected_tool": "route_stats", "expected_args": {"route": "1"}}
                ),
                _json.dumps(
                    {"id": "b", "question": "y", "expected_tool": "route_stats", "expected_args": {"route": "1"}}
                ),
                _json.dumps({"id": "c", "question": "z", "expected_tool": "time_series", "expected_args": {}}),
            ]
        )
    )
    _router.set_golden_set_path(p)

    class _E:
        available = True

        def embed(self, *a, **k):
            return [0.0] * 384

    monkeypatch.setattr(_router, "_get_embedder", lambda: _E())

    async def near_same(conn, agency_id, qvec, k):
        return [Match("a", "1の遅延", "", {}, 0.05), Match("b", "1の遅延状況", "", {}, 0.055)]

    monkeypatch.setattr("pipeline.query.rag_index.nearest", near_same)
    dec, _ex = await _router.route_or_examples("1の遅延は？", None, 1)
    assert dec is not None and dec.tool == "route_stats"  # same tool → dispatch despite 0.005 margin

    async def near_diff(conn, agency_id, qvec, k):
        return [Match("a", "1の遅延", "", {}, 0.05), Match("c", "傾向", "", {}, 0.055)]

    monkeypatch.setattr("pipeline.query.rag_index.nearest", near_diff)
    dec2, _ex2 = await _router.route_or_examples("1の遅延は？", None, 1)
    assert dec2 is None  # different tools within margin → ambiguous → fall through

    _router.set_golden_set_path(None)


class _AnyEmbedder:
    available = True

    def embed(self, *a, **k):
        return [0.0] * 384


@pytest.fixture
def stage2(monkeypatch, tmp_path):
    """Stage 2 over a given golden set and a stubbed nearest(): returns a
    setter taking the golden entries and the (chunk_id, content, distance)
    rows nearest() should return."""
    from pipeline.query import router as _router
    from pipeline.query.rag_index import Match

    monkeypatch.setattr(_router, "_get_embedder", lambda: _AnyEmbedder())

    def setup(golden, rows):
        p = tmp_path / "g.jsonl"
        p.write_text("\n".join(_json.dumps(g) for g in golden))
        _router.set_golden_set_path(p)

        async def near(conn, agency_id, qvec, k):
            return [Match(cid, content, "", {}, dist) for cid, content, dist in rows]

        monkeypatch.setattr("pipeline.query.rag_index.nearest", near)

    yield setup
    _router.set_golden_set_path(None)


def _entry(cid, question, tool, args):
    return {"id": cid, "question": question, "expected_tool": tool, "expected_args": args}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "matched, args, question, dispatched",
    [
        ("22171の運行情報を教えて", {"route": "22171"}, "22171の運行情報を見せて", True),
        ("22171の運行情報を教えて", {"route": "22171"}, "２２１７１の運行情報を教えて", True),
        ("22171の運行情報を教えて", {"route": "22171"}, "22172の運行情報を教えて", False),
        ("22171の運行情報を教えて", {"route": "22171"}, "22171と22172の運行情報", False),
        ("A1の遅延を教えて", {"route": "1021"}, "A2の遅延を教えて", False),
        ("5分以内の定時率を出して", {"threshold_min": 5}, "15分以内の定時率を出して", False),
        ("中央大橋の遅延", {"stop": "中央大橋"}, "県庁前の遅延", False),
        ("やばい系統教えて", {"metric": "avg_delay"}, "やばい系統を教えて", True),
    ],
)
async def test_embedding_dispatch_replays_args_only_for_the_same_entities(stage2, matched, args, question, dispatched):
    """Stage 2 replays the matched question's args verbatim, and those name
    its own route, alias, threshold or stop: a near-identical question about
    another one must reach the LLM, with the match as a few-shot example."""
    from pipeline.query import router as _router

    stage2([_entry("g", matched, "route_stats", args)], [("g", matched, 0.03)])
    dec, examples = await _router.route_or_examples(question, None, 1)
    if dispatched:
        assert dec is not None and (dec.tool, dec.args) == ("route_stats", args)
    else:
        assert dec is None
        assert [(e.chunk_id, e.args) for e in examples] == [("g", args)]


@pytest_asyncio.fixture
async def promoted_cache(apply_schema):
    """An agency with one promoted ask_intent_cache row and one that was
    never promoted."""
    pool = await _test_pool()
    async with pool.acquire() as conn:
        agency_id = await conn.fetchval(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        now = datetime.now(timezone.utc)
        await conn.executemany(
            "INSERT INTO ask_intent_cache "
            "(signature_hash, tool, args, confidence, last_question, agency_id, promoted_at) "
            "VALUES ($1, 'route_stats', $2::jsonb, 0.9, $3, $4, $5)",
            [
                ("00000000000000aa", '{"route": "22171"}', "22171の遅延を教えて", agency_id, now),
                ("00000000000000bb", '{"route": "16071"}', "16071の遅延を教えて", agency_id, None),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_a_promoted_cache_chunk_dispatches_its_own_tool_and_args(stage2, promoted_cache):
    """Promotion writes `cache_<signature_hash>` rag_chunks rows, whose
    tool/args live in ask_intent_cache, not golden_set.jsonl."""
    from pipeline.query import router as _router

    pool, agency_id = promoted_cache
    golden = [_entry("g", "22171の遅延状況は？", "route_stats", {"route": "22171"})]
    stage2(golden, [("cache_00000000000000aa", "22171の遅延を教えて", 0.0), ("g", "22171の遅延状況は？", 0.01)])
    async with pool.acquire() as conn:
        dec, _ = await _router.route_or_examples("22171の遅延を教えて", conn, agency_id)
    assert dec is not None
    assert (dec.tool, dec.args, dec.matched_pattern) == ("route_stats", {"route": "22171"}, "cache_00000000000000aa")


@pytest.mark.asyncio
async def test_an_unresolvable_chunk_neither_dispatches_nor_blocks_a_golden_match(stage2, promoted_cache):
    """A chunk with no tool/args behind it (a cache row never promoted, or
    gone) is skipped: it is not a runner-up that makes a golden match look
    ambiguous, and it takes no few-shot slot."""
    from pipeline.query import router as _router

    pool, agency_id = promoted_cache
    golden = [
        _entry("g", "22171の遅延状況は？", "route_stats", {"route": "22171"}),
        _entry("t", "直近の傾向", "time_series", {}),
    ]
    stage2(golden, [("g", "22171の遅延状況は？", 0.01), ("cache_00000000000000bb", "16071の遅延を教えて", 0.015)])
    async with pool.acquire() as conn:
        dec, _ = await _router.route_or_examples("22171の遅延状況は", conn, agency_id)
        assert dec is not None and dec.matched_pattern == "g"

        stage2(golden, [("cache_00000000000000bb", "16071の遅延を教えて", 0.2), ("t", "直近の傾向", 0.3)])
        dec, examples = await _router.route_or_examples("全然違う質問", conn, agency_id)
    assert dec is None
    assert [e.chunk_id for e in examples] == ["t"]


@pytest.mark.parametrize(
    "q",
    [
        "もっと見せて",
        "もう少し",
        "続き",
        "次の50件",
        "次のページ",
        "2ページ目",
        "残り",
        "他には",
        "さらに",
        "前のと逆順で",
        "同じ条件で先月",
        "それを詳しく",
        "降順",
        "昇順",
        "逆に",
        "絞り込んで",
        "show me more",
        "next",
        "again",
        "reverse",
    ],
)
def test_is_follow_up_true(q):
    assert is_follow_up(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "どんな路線がある？",
        "22171の遅延",
        "定時率TOP10",
        "停留所はいくつ？",
        "もっとも遅延が多い系統",
        "次の停留所は？",
        "前の停留所",
        "別府の路線",
        "same-day comparison",
        "next stop information",
        "",
    ],
)
def test_is_follow_up_false(q):
    assert is_follow_up(q) is False
