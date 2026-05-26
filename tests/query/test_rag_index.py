import asyncpg
import os
import pytest

from pipeline.query.rag_index import Match, nearest

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
