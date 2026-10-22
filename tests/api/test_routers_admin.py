"""End-to-end tests for ``/api/admin/users`` (list, detail, patch, delete) covering
the self-guard, last-admin guard, suspend kills sessions, and soft-delete anonymization.
"""

import asyncio
import json
import secrets
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
async def test_admin_user_sessions_omit_expired_sessions(admin_client, aconn):
    admin_sid, _admin_uid, _ = await _seed(aconn, role="admin")
    _sid, uid, _ = await _seed(aconn)
    await aconn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(f"expired-{uid}"),
        uid,
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    r = await admin_client.get(f"/api/admin/users/{uid}/sessions", cookies={"sid": admin_sid})
    assert r.status_code == 200
    assert len(r.json()) == 1


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
    assert body["byok_provider"] is None


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


@pytest.mark.asyncio
async def test_bulk_patch_writes_the_same_per_user_trail_as_the_single_endpoint(admin_client, aconn):
    """A durable record of who was suspended must not depend on which
    control the operator used; the bulk path touches the most rows."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn, role="user")
    _, uid_b, _ = await _seed(aconn, role="user")
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_b], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    kinds = await aconn.fetch(
        "SELECT user_id, kind FROM login_events WHERE user_id = ANY($1::int[]) ORDER BY user_id",
        [uid_a, uid_b],
    )
    assert [(row["user_id"], row["kind"]) for row in kinds] == [(uid_a, "suspended"), (uid_b, "suspended")]


@pytest.mark.asyncio
async def test_bulk_patch_records_nothing_for_an_id_the_patch_does_not_change(admin_client, aconn):
    """A no-op id in a batch must not manufacture an audit event."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_already, _ = await _seed(aconn, role="user")
    await aconn.execute("UPDATE users SET suspended_at = now() WHERE user_id=$1", uid_already)
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_already], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    count = await aconn.fetchval("SELECT count(*) FROM login_events WHERE user_id=$1", uid_already)
    assert count == 0


@pytest.mark.asyncio
async def test_user_detail_reports_byok_provider_without_the_key(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_target, _ = await _seed(aconn, email="byok@x")
    await aconn.execute(
        "INSERT INTO user_llm_keys (user_id, provider, encrypted_key, key_suffix) VALUES ($1, 'openai', $2, 'abcd')",
        uid_target,
        b"not-a-real-ciphertext",
    )
    r = await admin_client.get(f"/api/admin/users/{uid_target}", cookies={"sid": sid_admin})
    assert r.status_code == 200
    body = r.json()
    assert body["byok_provider"] == "openai"
    assert "key" not in body
    assert "abcd" not in str(body)


@pytest.mark.asyncio
async def test_user_activity_sums_the_window_per_route(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn, email="usage@x")
    await aconn.execute(
        """
        INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)
        VALUES ($1, current_date,      '/api/{agency_id}/overview/summary', 'GET', 1, false, 3, 1),
               ($1, current_date - 1,  '/api/{agency_id}/overview/summary', 'GET', 1, false, 2, 0),
               ($1, current_date - 40, '/api/{agency_id}/overview/summary', 'GET', 1, false, 9, 9)
        """,
        uid,
    )
    r = await admin_client.get(f"/api/admin/users/{uid}/activity?days=30", cookies={"sid": sid_admin})
    assert r.status_code == 200
    assert r.json() == [
        {"route": "/api/{agency_id}/overview/summary", "method": "GET", "agency_id": 1, "requests": 5, "errors": 1}
    ]


@pytest.mark.asyncio
async def test_user_activity_is_admin_only_and_bounded(admin_client, aconn):
    sid_user, uid, _ = await _seed(aconn, role="user")
    assert (await admin_client.get(f"/api/admin/users/{uid}/activity", cookies={"sid": sid_user})).status_code == 403
    sid_admin, _, _ = await _seed(aconn, role="admin")
    r = await admin_client.get(f"/api/admin/users/{uid}/activity?days=366", cookies={"sid": sid_admin})
    assert r.status_code == 422


_ORIGINAL_SUSPENSION = datetime(2020, 1, 1, tzinfo=timezone.utc)


async def _seed_suspended_long_ago(aconn):
    _, uid, _ = await _seed(aconn)
    await aconn.execute("UPDATE users SET suspended_at = $2 WHERE user_id=$1", uid, _ORIGINAL_SUSPENSION)
    return uid


@pytest.mark.asyncio
async def test_single_patch_of_other_field_keeps_original_suspended_at(admin_client, aconn):
    """suspended_at records when the suspension began; a patch that leaves the
    suspended state alone must not re-stamp it."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    uid = await _seed_suspended_long_ago(aconn)
    for body in ({"llm_approved": True}, {"suspended": True}):
        r = await admin_client.patch(
            f"/api/admin/users/{uid}",
            json=body,
            cookies={"sid": sid_admin},
            headers={"Origin": "http://test"},
        )
        assert r.status_code == 200
        assert await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid) == _ORIGINAL_SUSPENSION


@pytest.mark.asyncio
async def test_bulk_patch_keeps_original_suspended_at_for_already_suspended_ids(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    uid = await _seed_suspended_long_ago(aconn)
    for patch in ({"suspended": True}, {"llm_approved": True}):
        r = await admin_client.patch(
            "/api/admin/users/bulk",
            json={"ids": [uid], "patch": patch},
            cookies={"sid": sid_admin},
            headers={"Origin": "http://test"},
        )
        assert r.status_code == 200
        assert await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid) == _ORIGINAL_SUSPENSION


@pytest.mark.asyncio
async def test_soft_delete_keeps_original_suspended_at_for_already_suspended_user(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    uid = await _seed_suspended_long_ago(aconn)
    r = await admin_client.delete(
        f"/api/admin/users/{uid}",
        cookies={"sid": sid_admin},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 204
    assert await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid) == _ORIGINAL_SUSPENSION


@pytest.mark.asyncio
async def test_suspension_transitions_still_stamp_and_clear_suspended_at(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn)
    headers = {"Origin": "http://test"}
    r = await admin_client.patch(
        f"/api/admin/users/{uid}", json={"suspended": True}, cookies={"sid": sid_admin}, headers=headers
    )
    assert r.status_code == 200
    stamped = await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid)
    assert stamped is not None and stamped > _ORIGINAL_SUSPENSION
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid], "patch": {"suspended": False}},
        cookies={"sid": sid_admin},
        headers=headers,
    )
    assert r.status_code == 200
    assert await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid) is None
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid], "patch": {"suspended": True}},
        cookies={"sid": sid_admin},
        headers=headers,
    )
    assert r.status_code == 200
    assert await aconn.fetchval("SELECT suspended_at FROM users WHERE user_id=$1", uid) > _ORIGINAL_SUSPENSION


_HEADERS = {"Origin": "http://test"}


async def _seed_key(conn, *, owner_user_id, owner_email, revoked_at=None):
    raw = f"sk_{secrets.token_urlsafe(16)}"
    key_id = await conn.fetchval(
        "INSERT INTO api_keys (key_hash, owner_user_id, owner_email, revoked_at) VALUES ($1, $2, $3, $4) RETURNING id",
        token_hash(raw),
        owner_user_id,
        owner_email,
        revoked_at,
    )
    return key_id, raw


async def _delete(client, sid, uid):
    return await client.delete(f"/api/admin/users/{uid}", cookies={"sid": sid}, headers=_HEADERS)


@pytest.mark.asyncio
async def test_soft_delete_masks_and_revokes_every_key_the_user_owns(admin_client, aconn):
    """Ownership is what erase_user and the account export use: a key issued
    to the account, or a legacy ownerless key recorded under its address in
    any case. An already-revoked key keeps the time it was revoked."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn, email="keyed@x")
    _, uid_other, _ = await _seed(aconn, email="other@x")
    live_id, live_raw = await _seed_key(aconn, owner_user_id=uid, owner_email="keyed@x")
    revoked_id, _ = await _seed_key(aconn, owner_user_id=uid, owner_email="keyed@x", revoked_at=_ORIGINAL_SUSPENSION)
    legacy_id, legacy_raw = await _seed_key(aconn, owner_user_id=None, owner_email="Keyed@X")
    other_id, other_raw = await _seed_key(aconn, owner_user_id=uid_other, owner_email="other@x")
    assert (await admin_client.get("/health", headers={"X-API-Key": legacy_raw})).status_code == 200

    assert (await _delete(admin_client, sid_admin, uid)).status_code == 204

    keys = {r["id"]: r for r in await aconn.fetch("SELECT id, owner_email, revoked_at FROM api_keys")}
    for key_id in (live_id, revoked_id, legacy_id):
        assert keys[key_id]["owner_email"] == f"deleted-{uid}@local"
        assert keys[key_id]["revoked_at"] is not None
    assert keys[revoked_id]["revoked_at"] == _ORIGINAL_SUSPENSION
    assert keys[other_id]["owner_email"] == "other@x"
    assert keys[other_id]["revoked_at"] is None
    for raw in (live_raw, legacy_raw):
        assert (await admin_client.get("/health", headers={"X-API-Key": raw})).status_code == 401
    assert (await admin_client.get("/health", headers={"X-API-Key": other_raw})).status_code == 200


@pytest.mark.asyncio
async def test_soft_delete_marks_the_row_deleted_and_audits_the_revoked_keys(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn, email="marked@x")
    key_id, _ = await _seed_key(aconn, owner_user_id=uid, owner_email="marked@x")
    assert (await _delete(admin_client, sid_admin, uid)).status_code == 204
    assert await aconn.fetchval("SELECT deleted_at FROM users WHERE user_id=$1", uid) is not None
    after = await aconn.fetchval(
        "SELECT after::text FROM admin_audit WHERE action = 'user.deleted' AND target_id = $1", str(uid)
    )
    assert json.loads(after)["api_keys_revoked"] == [key_id]
    assert "marked@x" not in after


@pytest.mark.asyncio
async def test_user_rows_report_deleted_at(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_live, _ = await _seed(aconn, email="live@x")
    _, uid_gone, _ = await _seed(aconn, email="gone@x")
    assert (await _delete(admin_client, sid_admin, uid_gone)).status_code == 204
    listed = (await admin_client.get("/api/admin/users", cookies={"sid": sid_admin})).json()["users"]
    by_id = {u["user_id"]: u for u in listed}
    assert by_id[uid_live]["deleted_at"] is None
    assert by_id[uid_gone]["deleted_at"] is not None
    detail = (await admin_client.get(f"/api/admin/users/{uid_gone}", cookies={"sid": sid_admin})).json()
    assert detail["deleted_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"suspended": False}, {"llm_approved": True}, {"role": "admin"}])
async def test_a_deleted_user_cannot_be_patched(admin_client, aconn, body):
    """Unsuspending would bring a deleted account's credentials back; no
    other edit to it means anything either."""
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn)
    assert (await _delete(admin_client, sid_admin, uid)).status_code == 204
    before = await aconn.fetchrow("SELECT role, suspended_at, llm_approved FROM users WHERE user_id=$1", uid)
    r = await admin_client.patch(f"/api/admin/users/{uid}", json=body, cookies={"sid": sid_admin}, headers=_HEADERS)
    assert r.status_code == 409
    assert r.json()["detail"] == "user is deleted"
    after = await aconn.fetchrow("SELECT role, suspended_at, llm_approved FROM users WHERE user_id=$1", uid)
    assert after == before


@pytest.mark.asyncio
async def test_bulk_patch_refuses_a_batch_holding_a_deleted_user(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid_live, _ = await _seed(aconn, suspended=True)
    _, uid_gone, _ = await _seed(aconn)
    assert (await _delete(admin_client, sid_admin, uid_gone)).status_code == 204
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_live, uid_gone], "patch": {"suspended": False}},
        cookies={"sid": sid_admin},
        headers=_HEADERS,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "user is deleted"
    rows = await aconn.fetch("SELECT suspended_at FROM users WHERE user_id = ANY($1::int[])", [uid_live, uid_gone])
    assert all(row["suspended_at"] is not None for row in rows)


@pytest.mark.asyncio
async def test_a_deleted_user_cannot_be_deleted_again(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn)
    assert (await _delete(admin_client, sid_admin, uid)).status_code == 204
    r = await _delete(admin_client, sid_admin, uid)
    assert r.status_code == 409
    assert r.json()["detail"] == "user is deleted"
    assert (
        await aconn.fetchval(
            "SELECT count(*) FROM admin_audit WHERE action = 'user.deleted' AND target_id = $1", str(uid)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_no_key_can_be_issued_to_a_deleted_user(admin_client, aconn):
    sid_admin, _, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn)
    assert (await _delete(admin_client, sid_admin, uid)).status_code == 204
    r = await admin_client.post(
        "/api/admin/api-keys", json={"owner_user_id": uid}, cookies={"sid": sid_admin}, headers=_HEADERS
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "user is deleted"
    assert await aconn.fetchval("SELECT count(*) FROM api_keys WHERE owner_user_id=$1", uid) == 0


_ACTOR_UA = "admin-console/1"
_ACTOR_HEADERS = {**_HEADERS, "User-Agent": _ACTOR_UA}


async def _actor_trail(conn, actor_id):
    """Every account event ``actor_id`` wrote, oldest first, with the address
    and browser it came from. ASGITransport reports the client as 127.0.0.1."""
    rows = await conn.fetch(
        "SELECT user_id, kind, host(ip) AS ip, user_agent FROM login_events WHERE actor_id = $1 ORDER BY event_id",
        actor_id,
    )
    return [(r["user_id"], r["kind"], r["ip"], r["user_agent"]) for r in rows]


@pytest.mark.asyncio
async def test_a_single_patch_records_the_admins_address_and_browser_on_every_event(admin_client, aconn):
    """An admin's change to another account is recorded with where it came
    from, as a sign-in is."""
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    _, uid, _ = await _seed(aconn)
    url = f"/api/admin/users/{uid}"
    changed = {"role": "admin", "suspended": True, "llm_approved": True}
    r = await admin_client.patch(url, json=changed, cookies={"sid": sid_admin}, headers=_ACTOR_HEADERS)
    assert r.status_code == 200
    r = await admin_client.patch(url, json={"suspended": False}, cookies={"sid": sid_admin}, headers=_ACTOR_HEADERS)
    assert r.status_code == 200
    assert await _actor_trail(aconn, uid_admin) == [
        (uid, kind, "127.0.0.1", _ACTOR_UA)
        for kind in ("suspended", "role_changed", "llm_approved_changed", "unsuspended")
    ]


@pytest.mark.asyncio
async def test_a_bulk_patch_records_the_admins_address_and_browser_on_every_event(admin_client, aconn):
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    _, uid_a, _ = await _seed(aconn)
    _, uid_b, _ = await _seed(aconn)
    r = await admin_client.patch(
        "/api/admin/users/bulk",
        json={"ids": [uid_a, uid_b], "patch": {"role": "admin", "suspended": True, "llm_approved": True}},
        cookies={"sid": sid_admin},
        headers=_ACTOR_HEADERS,
    )
    assert r.status_code == 200
    assert await _actor_trail(aconn, uid_admin) == [
        (uid, kind, "127.0.0.1", _ACTOR_UA)
        for uid in (uid_a, uid_b)
        for kind in ("suspended", "role_changed", "llm_approved_changed")
    ]


@pytest.mark.asyncio
async def test_session_key_invite_and_delete_actions_record_the_admins_address_and_browser(admin_client, aconn):
    sid_admin, uid_admin, _ = await _seed(aconn, role="admin")
    sid, uid, _ = await _seed(aconn)
    kw = {"cookies": {"sid": sid_admin}, "headers": _ACTOR_HEADERS}
    r = await admin_client.delete(f"/api/admin/users/{uid}/sessions/{token_hash(sid)[:16]}", **kw)
    assert r.status_code == 204
    r = await admin_client.post("/api/admin/api-keys", json={"owner_user_id": uid}, **kw)
    assert r.status_code == 201
    assert (await admin_client.delete(f"/api/admin/api-keys/{r.json()['id']}", **kw)).status_code == 204
    r = await admin_client.post("/api/admin/invites", json={"email": "invitee@example.com"}, **kw)
    assert r.status_code == 201
    assert (await admin_client.delete(f"/api/admin/users/{uid}", **kw)).status_code == 204
    assert await _actor_trail(aconn, uid_admin) == [
        (uid, "session_revoked", "127.0.0.1", _ACTOR_UA),
        (uid, "api_key_issued", "127.0.0.1", _ACTOR_UA),
        (uid, "api_key_revoked", "127.0.0.1", _ACTOR_UA),
        (None, "invite_created", "127.0.0.1", _ACTOR_UA),
        (uid, "deleted", "127.0.0.1", _ACTOR_UA),
    ]
