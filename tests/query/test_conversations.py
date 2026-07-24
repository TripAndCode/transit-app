"""ask_conversations + ask_conversation_messages async DAL tests."""

from __future__ import annotations

import os

import asyncpg
import pytest

from pipeline.query.conversations import (
    PermissionDenied,
    append_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    migrate_anon_threads,
    update_conversation,
)

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture
async def pool_with_users(apply_schema):
    """Pool + two test users + one agency."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        a = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        u1 = await c.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('u1@test', 'U1', 'user') RETURNING user_id"
        )
        u2 = await c.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('u2@test', 'U2', 'user') RETURNING user_id"
        )
    yield pool, a["agency_id"], u1["user_id"], u2["user_id"]
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        await c.execute("DELETE FROM users WHERE email LIKE 'u_@test'")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_create_and_get_conversation(pool_with_users):
    pool, agency, u1, _u2 = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="Test", filter_ctx={"dow": "weekday"})
    assert conv["title"] == "Test"
    assert conv["user_id"] == u1
    async with pool.acquire() as c:
        fetched = await get_conversation(c, conv["conversation_id"], user_id=u1, agency_id=agency)
    assert fetched["conversation_id"] == conv["conversation_id"]


@pytest.mark.asyncio
async def test_get_conversation_other_user_raises(pool_with_users):
    pool, agency, u1, u2 = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="X", filter_ctx={})
        with pytest.raises(PermissionDenied):
            await get_conversation(c, conv["conversation_id"], user_id=u2, agency_id=agency)


@pytest.mark.asyncio
async def test_get_conversation_wrong_agency_raises(pool_with_users):
    """Same owner, wrong agency_id must still raise PermissionDenied - the
    ownership check must verify agency_id, not just user_id."""
    pool, agency, u1, _u2 = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="X", filter_ctx={})
        other_agency = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Other', 'http://other') RETURNING agency_id"
        )
        with pytest.raises(PermissionDenied):
            await get_conversation(c, conv["conversation_id"], user_id=u1, agency_id=other_agency["agency_id"])


@pytest.mark.asyncio
async def test_list_conversations_ordered_by_updated(pool_with_users):
    pool, agency, u1, _ = pool_with_users
    async with pool.acquire() as c:
        await create_conversation(c, user_id=u1, agency_id=agency, title="A", filter_ctx={})
        await create_conversation(c, user_id=u1, agency_id=agency, title="B", filter_ctx={})
        rows = await list_conversations(c, user_id=u1, agency_id=agency, limit=10)
    titles = [r["title"] for r in rows]
    assert titles == ["B", "A"]


@pytest.mark.asyncio
async def test_update_conversation_owner_only(pool_with_users):
    pool, agency, u1, u2 = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="X", filter_ctx={})
        await update_conversation(
            c, conv["conversation_id"], user_id=u1, agency_id=agency, title="renamed", pinned=True
        )
        refreshed = await get_conversation(c, conv["conversation_id"], user_id=u1, agency_id=agency)
        assert refreshed["title"] == "renamed" and refreshed["pinned"] is True

        with pytest.raises(PermissionDenied):
            await update_conversation(c, conv["conversation_id"], user_id=u2, agency_id=agency, title="hijacked")


@pytest.mark.asyncio
async def test_delete_conversation_cascades_messages(pool_with_users):
    pool, agency, u1, _ = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="X", filter_ctx={})
        await append_message(
            c,
            conv["conversation_id"],
            role="user",
            chip_id="meta-routes",
            tool=None,
            args=None,
            signature_hash=None,
            result=None,
            rendered_summary="路線一覧",
        )
        await delete_conversation(c, conv["conversation_id"], user_id=u1, agency_id=agency)
    async with pool.acquire() as c:
        msgs = await c.fetch(
            "SELECT COUNT(*) AS n FROM ask_conversation_messages WHERE conversation_id=$1", conv["conversation_id"]
        )
    assert msgs[0]["n"] == 0


@pytest.mark.asyncio
async def test_append_and_list_messages(pool_with_users):
    pool, agency, u1, _ = pool_with_users
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title="X", filter_ctx={})
        await append_message(
            c,
            conv["conversation_id"],
            role="user",
            chip_id="rank-delay-top",
            tool=None,
            args=None,
            signature_hash=None,
            result=None,
            rendered_summary=None,
        )
        await append_message(
            c,
            conv["conversation_id"],
            role="assistant",
            chip_id="rank-delay-top",
            tool="top_n",
            args={"metric": "avg_delay", "n": 10},
            signature_hash="abcdef0123456789",
            result={"kind": "table"},
            rendered_summary="遅延ランキングTOP10: ...",
        )
        msgs = await list_messages(c, conv["conversation_id"], user_id=u1, agency_id=agency)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["tool"] == "top_n"
    assert msgs[1]["args"] == {"metric": "avg_delay", "n": 10}


@pytest.mark.asyncio
async def test_migrate_anon_threads_idempotent(pool_with_users):
    """Migrating the same anonymous payload twice should not create duplicates."""
    pool, agency, u1, _ = pool_with_users
    payload = [
        {
            "client_id": "anon-thread-1",
            "title": "Anon A",
            "filter_ctx": {"dow": "weekday"},
            "pinned": False,
            "created_at": "2026-05-29T10:00:00",
            "updated_at": "2026-05-29T10:00:00",
            "messages": [],
        },
        {
            "client_id": "anon-thread-2",
            "title": "Anon B",
            "filter_ctx": {},
            "pinned": True,
            "created_at": "2026-05-29T11:00:00",
            "updated_at": "2026-05-29T11:00:00",
            "messages": [],
        },
    ]
    async with pool.acquire() as c:
        first = await migrate_anon_threads(c, user_id=u1, agency_id=agency, threads=payload)
        second = await migrate_anon_threads(c, user_id=u1, agency_id=agency, threads=payload)
    assert first == 2
    assert second == 0
    async with pool.acquire() as c:
        rows = await list_conversations(c, user_id=u1, agency_id=agency, limit=10)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_migrate_homes_each_thread_to_its_own_agency(pool_with_users):
    """Each anon thread carries its own agency_id; migration must home it there,
    not dump every thread under the URL agency the user happened to be viewing."""
    pool, agency1, u1, _ = pool_with_users
    async with pool.acquire() as c:
        a2 = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T2', 'http://t2') RETURNING agency_id"
        )
        agency2 = a2["agency_id"]
    payload = [
        {
            "client_id": "t-a1",
            "agency_id": agency1,
            "title": "for a1",
            "filter_ctx": {},
            "pinned": False,
            "created_at": "2026-05-29T10:00:00",
            "updated_at": "2026-05-29T10:00:00",
            "messages": [],
        },
        {
            "client_id": "t-a2",
            "agency_id": agency2,
            "title": "for a2",
            "filter_ctx": {},
            "pinned": False,
            "created_at": "2026-05-29T11:00:00",
            "updated_at": "2026-05-29T11:00:00",
            "messages": [],
        },
    ]
    # URL/scope agency is agency1, but thread t-a2 belongs to agency2.
    async with pool.acquire() as c:
        n = await migrate_anon_threads(c, user_id=u1, agency_id=agency1, threads=payload)
    assert n == 2
    async with pool.acquire() as c:
        a1_rows = await list_conversations(c, user_id=u1, agency_id=agency1, limit=10)
        a2_rows = await list_conversations(c, user_id=u1, agency_id=agency2, limit=10)
    assert {r["title"] for r in a1_rows} == {"for a1"}
    assert {r["title"] for r in a2_rows} == {"for a2"}


@pytest.mark.asyncio
async def test_title_truncated_to_200(pool_with_users):
    pool, agency, u1, _ = pool_with_users
    long = "x" * 5000
    async with pool.acquire() as c:
        conv = await create_conversation(c, user_id=u1, agency_id=agency, title=long, filter_ctx={})
    assert len(conv["title"]) == 200
