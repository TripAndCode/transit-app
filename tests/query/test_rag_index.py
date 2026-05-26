import asyncpg
import hashlib
import os
from pathlib import Path

import pytest

from pipeline.query.rag_index import Match, build_index, nearest

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def conn_with_chunks(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        # Three chunks with synthetic 384-dim vectors. The first vector is
        # close to query [1,0,0,...]; the second to [0,1,0,...]; third to
        # [0,0,1,...]. pgvector cosine should return them in that order.
        def vec(axis: int):
            v = [0.0] * 384
            v[axis] = 1.0
            return v

        await conn.executemany(
            "INSERT INTO rag_chunks (chunk_id, agency_id, content, embedding, content_hash) "
            "VALUES ($1, $2, $3, $4::vector, $5)",
            [
                ("c-x", agency_id, "X axis chunk", str(vec(0)), "hx"),
                ("c-y", agency_id, "Y axis chunk", str(vec(1)), "hy"),
                ("c-z", agency_id, "Z axis chunk", str(vec(2)), "hz"),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_nearest_returns_closest_first(conn_with_chunks):
    pool, agency_id = conn_with_chunks
    qvec = [1.0] + [0.0] * 383  # close to c-x
    async with pool.acquire() as conn:
        rows = await nearest(conn, agency_id, qvec, k=2)
    assert isinstance(rows[0], Match)
    assert rows[0].chunk_id == "c-x"
    assert rows[0].distance < 0.01
    assert len(rows) == 2
    assert rows[1].chunk_id in ("c-y", "c-z")


@pytest.mark.asyncio
async def test_nearest_scopes_by_agency(conn_with_chunks):
    pool, agency_id = conn_with_chunks
    async with pool.acquire() as conn:
        rows = await nearest(conn, agency_id + 9999, [1.0] + [0.0] * 383, k=3)
    assert rows == []


import json as _json
import tempfile


class _FakeEmbedder:
    """Deterministic embedder for build_index tests — avoids the real model."""

    available = True

    def embed(self, text: str, *, mode: str) -> list[float]:
        # Hash text to 384-d vector: each dim is sin(hash_byte + i).
        import math
        h = hashlib.sha256(text.encode()).digest()
        return [math.sin((h[i % 32] / 255.0) + i * 0.001) for i in range(384)]


@pytest.fixture
async def conn_clean(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T','http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _write_golden(*rows):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for r in rows:
        f.write(_json.dumps(r) + "\n")
    f.close()
    return Path(f.name)


@pytest.mark.asyncio
async def test_build_index_inserts_then_skips(conn_clean):
    pool, agency_id = conn_clean
    path = _write_golden(
        {"id": "q-001", "question": "test one", "expected_tool": "describe_data", "expected_args": {"kind": "routes"}},
        {"id": "q-002", "question": "test two", "expected_tool": "top_n", "expected_args": {"metric": "avg_delay"}},
    )
    async with pool.acquire() as conn:
        first = await build_index(conn, agency_id, path, embedder=_FakeEmbedder())
    assert first == {"inserted": 2, "updated": 0, "skipped": 0}

    async with pool.acquire() as conn:
        second = await build_index(conn, agency_id, path, embedder=_FakeEmbedder())
    assert second == {"inserted": 0, "updated": 0, "skipped": 2}


@pytest.mark.asyncio
async def test_build_index_updates_on_text_change(conn_clean):
    pool, agency_id = conn_clean
    path1 = _write_golden(
        {"id": "q-001", "question": "original", "expected_tool": "top_n", "expected_args": {}},
    )
    async with pool.acquire() as conn:
        await build_index(conn, agency_id, path1, embedder=_FakeEmbedder())

    path2 = _write_golden(
        {"id": "q-001", "question": "rephrased", "expected_tool": "top_n", "expected_args": {}},
    )
    async with pool.acquire() as conn:
        result = await build_index(conn, agency_id, path2, embedder=_FakeEmbedder())
    assert result == {"inserted": 0, "updated": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_build_index_skips_lines_without_id_or_question(conn_clean):
    pool, agency_id = conn_clean
    path = _write_golden(
        {"id": "q-001", "question": "valid", "expected_tool": "top_n", "expected_args": {}},
        {"id": "q-002", "expected_tool": "top_n", "expected_args": {}},  # missing question
        {"question": "no id", "expected_tool": "top_n", "expected_args": {}},  # missing id
    )
    async with pool.acquire() as conn:
        result = await build_index(conn, agency_id, path, embedder=_FakeEmbedder())
    assert result["inserted"] == 1
