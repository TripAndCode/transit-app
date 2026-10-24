"""End-to-end tests for ``/api/admin/flags`` (list, override, clear), covering
the admin guard, the mandatory-reason PATCH contract, the returned provenance
(env vs override), and that a write takes effect immediately (no 30s cache
lag) for both the API's own next GET and `pipeline.flags.flag()`.
"""

import httpx
import pytest
from httpx import ASGITransport

from pipeline.flags import REGISTRY, flag
from tests.conftest import _test_pool
from tests.fixtures.users import seed_session


@pytest.fixture
async def admin_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_non_admin_forbidden(admin_client, aconn):
    sid = (await seed_session(aconn, role="user")).sid
    r = await admin_client.get("/api/admin/flags", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_returns_every_registered_flag(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await admin_client.get("/api/admin/flags", cookies={"sid": sid})
    assert r.status_code == 200
    body = r.json()
    assert {row["key"] for row in body} == {d.key for d in REGISTRY}
    for row in body:
        assert row["source"] in ("env", "override")


@pytest.mark.asyncio
async def test_patch_requires_reason(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    key = REGISTRY[0].key
    r = await admin_client.patch(
        f"/api/admin/flags/{key}",
        json={"value": True},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_blank_reason(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    key = REGISTRY[0].key
    r = await admin_client.patch(
        f"/api/admin/flags/{key}",
        json={"value": True, "reason": "   "},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_unknown_key_404(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await admin_client.patch(
        "/api/admin/flags/not_a_real_flag",
        json={"value": True, "reason": "testing"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_sets_override_visible_immediately(admin_client, aconn, monkeypatch):
    sid, uid, _ = await seed_session(aconn, role="admin")
    definition = REGISTRY[0]
    monkeypatch.delenv(definition.env_var, raising=False)
    override_value = not definition.env_default

    r = await admin_client.patch(
        f"/api/admin/flags/{definition.key}",
        json={"value": override_value, "reason": "load-test rollout"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["value"] is override_value
    assert body["source"] == "override"
    assert body["updated_by"] == uid
    assert body["reason"] == "load-test rollout"

    # No 30s cache lag: both the API's own next GET and a direct flag() call
    # (as a gated feature would make) see the override right away.
    r2 = await admin_client.get("/api/admin/flags", cookies={"sid": sid})
    patched = next(row for row in r2.json() if row["key"] == definition.key)
    assert patched["value"] is override_value
    assert flag(definition.key, definition.env_default) is override_value


@pytest.mark.asyncio
async def test_patch_records_admin_action(admin_client, aconn, monkeypatch):
    calls = []

    async def _fake_record_admin_action(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("api.routers.admin_flags.record_admin_action", _fake_record_admin_action)
    sid, uid, _ = await seed_session(aconn, role="admin")
    definition = REGISTRY[0]

    r = await admin_client.patch(
        f"/api/admin/flags/{definition.key}",
        json={"value": True, "reason": "audit check"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    assert len(calls) == 1
    assert calls[0]["actor_id"] == uid
    assert calls[0]["action"] == "flag.set"
    assert calls[0]["target_type"] == "feature_flag"
    assert calls[0]["target_id"] == definition.key
    assert calls[0]["reason"] == "audit check"
    assert calls[0]["after"] == {"value": True}


@pytest.mark.asyncio
async def test_delete_unknown_key_404(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await admin_client.delete(
        "/api/admin/flags/not_a_real_flag",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_clears_the_override_and_restores_the_env_value(admin_client, aconn, monkeypatch):
    sid = (await seed_session(aconn, role="admin")).sid
    definition = REGISTRY[0]
    monkeypatch.delenv(definition.env_var, raising=False)

    r = await admin_client.patch(
        f"/api/admin/flags/{definition.key}",
        json={"value": not definition.env_default, "reason": "incident rollback"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200

    r2 = await admin_client.delete(
        f"/api/admin/flags/{definition.key}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["source"] == "env"
    assert body["value"] is definition.env_default
    assert body["updated_by"] is None
    assert body["reason"] is None

    row = await aconn.fetchrow("SELECT 1 FROM feature_flags WHERE key = $1", definition.key)
    assert row is None
    assert flag(definition.key, definition.env_default) is definition.env_default


@pytest.mark.asyncio
async def test_delete_records_admin_action(admin_client, aconn, monkeypatch):
    calls = []

    async def _fake_record_admin_action(conn, **kwargs):
        calls.append(kwargs)

    sid, uid, _ = await seed_session(aconn, role="admin")
    definition = REGISTRY[0]
    monkeypatch.delenv(definition.env_var, raising=False)
    r = await admin_client.patch(
        f"/api/admin/flags/{definition.key}",
        json={"value": not definition.env_default, "reason": "incident rollback"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200

    monkeypatch.setattr("api.routers.admin_flags.record_admin_action", _fake_record_admin_action)
    r2 = await admin_client.delete(
        f"/api/admin/flags/{definition.key}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 200
    assert len(calls) == 1
    assert calls[0]["actor_id"] == uid
    assert calls[0]["action"] == "flag.cleared"
    assert calls[0]["target_type"] == "feature_flag"
    assert calls[0]["target_id"] == definition.key
    assert calls[0]["after"] == {"value": definition.env_default}


@pytest.mark.asyncio
async def test_delete_without_an_override_is_a_no_op(admin_client, aconn, monkeypatch):
    sid = (await seed_session(aconn, role="admin")).sid
    definition = REGISTRY[0]
    monkeypatch.delenv(definition.env_var, raising=False)
    await aconn.execute("DELETE FROM feature_flags WHERE key = $1", definition.key)

    r = await admin_client.delete(
        f"/api/admin/flags/{definition.key}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "env"


@pytest.mark.asyncio
async def test_delete_rejects_a_cross_origin_request(admin_client, aconn):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await admin_client.delete(
        f"/api/admin/flags/{REGISTRY[0].key}",
        cookies={"sid": sid},
        headers={"Origin": "http://evil.test"},
    )
    assert r.status_code == 403
