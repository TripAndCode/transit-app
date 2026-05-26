import json as _json
import os

import asyncpg
import pytest
import pytest_asyncio

from pipeline.query.router import (
    _match_rules,
    retrieve_examples,
    route_question,
    set_golden_set_path,
)

DATABASE_URL = os.environ["DATABASE_URL"]


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
    pool = await asyncpg.create_pool(DATABASE_URL)
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
    """Distant query → distance > threshold → returns None."""
    pool, agency_id = conn_with_embedded_chunks
    async with pool.acquire() as conn:
        decision = await route_question("天気はどう？", conn, agency_id)
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
        matches = await retrieve_examples("天気はどう？", conn, agency_id, k=3)
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
