"""End-to-end tests for ``/api/admin/users`` (list, detail, patch, delete) covering
the self-guard, last-admin guard, suspend kills sessions, and soft-delete anonymization.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.security import token_hash
from tests.conftest import _test_pool


async def _seed(conn, *, role="user", email=None, suspended=False):
    email = email or f"u{datetime.now().timestamp()}@x"
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role, suspended_at) VALUES ($1, $2, $3) RETURNING user_id",
            email,
            role,
            datetime.now(timezone.utc) if suspended else None,
        )
    )["user_id"]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=30),
    )
    return sid, uid, email


@pytest.fixture
async def admin_client(apply_schema):
    from api.main import app

    # min_size=2: test_last_admin_guard_survives_concurrent_demotes fires two
    # concurrent requests via asyncio.gather and needs both to already hold a
    # live connection, or the second pays a real connect round trip before
    # its handler starts, turning the intended race into an effectively
    # sequential run.
    pool = await _test_pool(min_size=2)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_non_admin_forbidden(admin_client, aconn):
    sid, _, _ = await _seed(aconn, role="user")
    r = await admin_client.get("/api/admin/users", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_lists_users(admin_client, aconn):
    sid, _, _ = await _seed(aconn, role="admin")
    await _seed(aconn, email="a@x")
    await _seed(aconn, email="b@x")
    r = await admin_client.get("/api/admin/users", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 3
    assert any(u["email"] == "a@x" for u in body["users"])


@pytest.mark.asyncio
async def test_admin_search(admin_client, aconn):
    sid, _, _ = await _seed(aconn, role="admin")
    await _seed(aconn, email="findme@x")
    r = await admin_client.get("/api/admin/users?q=findme", cookies={"sid": sid})
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_admin_cannot_modify_self(admin_client, aconn):
    sid, uid, _ = await _seed(aconn, role="admin")
    r = await admin_client.patch(
        f"/api/admin/users/{uid}",
        json={"role": "user"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 400
    assert "self" in r.json()["detail"]


@pytest.mark.asyncio
async def test_admin_last_admin_guard(admin_client, aconn):
    """Demoting or suspending the final remaining admin must be rejected."""
    sid_a, uid_a, _ = await _seed(aconn, role="admin")
    _sid_b, uid_b, _ = await _seed(aconn, role="admin")
    # admin A demotes B -> leaves one admin (A) -- allowed
    r = await admin_client.patch(
        f"/api/admin/users/{uid_b}",
        json={"role": "user"},
        cookies={"sid": sid_a},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    # now A tries to demote themselves via DELETE on themselves -- blocked by self-guard
    r2 = await admin_client.delete(
        f"/api/admin/users/{uid_a}",
        cookies={"sid": sid_a},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_last_admin_guard_survives_concurrent_demotes(admin_client, aconn):
    """Two admins concurrently demoting *each other* must never both
    succeed — the last-admin guard's count-then-update must be atomic
    against another in-flight demote, not just against edits to the same
    row. Runs each interleaving several times since it's a real race:
    a single pass could get lucky even against the buggy TOCTOU code."""
    for _ in range(8):
        # A demotion that correctly loses the race is *supposed* to leave its
        # admin in place - without this reset, that legitimate survivor
        # carries into the next iteration as a 3rd admin, which would let
        # both concurrent demotes succeed for a completely different, valid
        # reason (a bystander admin still active) and defeat this test.
        await aconn.execute("DELETE FROM users")
        sid_a, uid_a, _ = await _seed(aconn, role="admin")
        sid_b, uid_b, _ = await _seed(aconn, role="admin")

        r_a, r_b = await asyncio.gather(
            admin_client.patch(
                f"/api/admin/users/{uid_b}",
                json={"role": "user"},
                cookies={"sid": sid_a},
                headers={"Origin": "http://test"},
            ),
            admin_client.patch(
                f"/api/admin/users/{uid_a}",
                json={"role": "user"},
                cookies={"sid": sid_b},
                headers={"Origin": "http://test"},
            ),
        )
        # At most one of the two concurrent demotions may succeed - the other
        # must be rejected by the last-admin guard once it's an admin's turn
        # to observe the other's already-committed change.
        successes = [r for r in (r_a, r_b) if r.status_code == 200]
        assert len(successes) <= 1, (r_a.status_code, r_b.status_code)

        remaining_admins = await aconn.fetchval(
            "SELECT count(*) FROM users WHERE role='admin' AND suspended_at IS NULL"
        )
        assert remaining_admins >= 1, "last-admin guard raced: zero active admins remain"


@pytest.mark.asyncio
async def test_suspend_kills_sessions(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    sid_target, uid_target, _ = await _seed(aconn)
    r = await admin_client.patch(
        f"/api/admin/users/{uid_target}",
        json={"suspended": True},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    n = await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid_target)
    assert n == 0
    # subsequent request from target gets 401
    r2 = await admin_client.get("/api/me", cookies={"sid": sid_target})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_soft_delete_anonymizes(admin_client, aconn):
    """Soft-deleting a user must scrub PII and kill their sessions."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _sid_target, uid_target, target_email = await _seed(aconn, email="target@x")
    r = await admin_client.delete(
        f"/api/admin/users/{uid_target}",
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 204
    row = await aconn.fetchrow("SELECT email, suspended_at FROM users WHERE user_id=$1", uid_target)
    assert row["email"] != target_email
    assert row["email"].startswith("deleted-")
    assert row["suspended_at"] is not None
    n = await aconn.fetchval("SELECT count(*) FROM oauth_identities WHERE user_id=$1", uid_target)
    assert n == 0


@pytest.mark.asyncio
async def test_user_detail(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_target, _ = await _seed(aconn, email="detail@x")
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id) VALUES ('google', 's1', $1)",
        uid_target,
    )
    r = await admin_client.get(f"/api/admin/users/{uid_target}", cookies={"sid": sid_admin})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "detail@x"
    assert any(i["provider"] == "google" for i in body["identities"])


@pytest.mark.asyncio
async def test_bulk_patch_approves_llm_for_every_id_in_one_transaction(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn, email="bulk-a@x")
    _, uid_b, _ = await _seed(aconn, email="bulk-b@x")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_b], "patch": {"llm_approved": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    body = r.json()
    assert {u["user_id"] for u in body} == {uid_a, uid_b}
    assert all(u["llm_approved"] for u in body)
    rows = await aconn.fetch("SELECT llm_approved FROM users WHERE user_id = ANY($1::int[])", [uid_a, uid_b])
    assert all(row["llm_approved"] for row in rows)


@pytest.mark.asyncio
async def test_bulk_patch_suspend_kills_sessions_for_every_target(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    sid_a, uid_a, _ = await _seed(aconn, email="bulk-susp-a@x")
    sid_b, uid_b, _ = await _seed(aconn, email="bulk-susp-b@x")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_b], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    n = await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id = ANY($1::int[])", [uid_a, uid_b])
    assert n == 0
    for sid in (sid_a, sid_b):
        r2 = await admin_client.get("/api/me", cookies={"sid": sid})
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_bulk_patch_refuses_to_demote_self_even_among_many_ids(admin_client, aconn):
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    _, uid_other, _ = await _seed(aconn, role="admin")
    _, uid_user, _ = await _seed(aconn)
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_other, uid_admin, uid_user], "patch": {"role": "user"}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 400
    assert "self" in r.json()["detail"]
    # Rejected atomically: not even the other ids were touched.
    row = await aconn.fetchrow("SELECT role FROM users WHERE user_id=$1", uid_other)
    assert row["role"] == "admin"


@pytest.mark.asyncio
async def test_bulk_patch_counts_the_whole_batch_before_deciding(admin_client, aconn):
    """The active-admin count is evaluated over every locked row with the
    patch applied, not one id at a time -- so suspending several admins at
    once is allowed exactly while one remains active.

    The caller is always an active admin and the self-guard keeps them out of
    a suspending batch, so this endpoint cannot actually reach zero; the
    "would leave no admins" branch is defence in depth for a future caller
    that is not the actor. What is reachable, and what this pins, is that the
    batch-wide count does not misfire on a legitimate multi-admin suspend.
    """
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn, role="admin")
    _, uid_b, _ = await _seed(aconn, role="admin")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_b], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    still_active = await aconn.fetchval(
        "SELECT array_agg(user_id ORDER BY user_id) FROM users WHERE role='admin' AND suspended_at IS NULL"
    )
    assert still_active == [uid_admin]


@pytest.mark.asyncio
async def test_bulk_patch_refuses_to_suspend_the_caller_among_others(admin_client, aconn):
    """The self-guard is what keeps the batch from reaching zero admins."""
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn, role="admin")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_admin], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 400
    assert "self" in r.json()["detail"]
    untouched = await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid_a)
    assert untouched is None


@pytest.mark.asyncio
async def test_bulk_patch_rejects_unknown_id(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn)
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, 999_999_999], "patch": {"llm_approved": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bulk_patch_rejects_more_than_200_ids(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": list(range(1, 202)), "patch": {"llm_approved": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_patch_non_admin_forbidden(admin_client, aconn):
    sid, _, _ = await _seed(aconn, role="user")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [1], "patch": {"llm_approved": True}},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_users_filters_by_llm_approved(admin_client, aconn):
    sid, _, _ = await _seed(aconn, role="admin")
    _, uid_pending, _ = await _seed(aconn, email="pending@x")
    r = await admin_client.get("/api/admin/users?llm_approved=false", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert any(u["user_id"] == uid_pending for u in body["users"])
    assert all(not u["llm_approved"] for u in body["users"])
