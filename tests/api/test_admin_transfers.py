"""``/api/admin/transfer-pairs``: admin-only CRUD of the hub transfers the
Transfers view reads, each mutation audited in its own transaction."""

import json

import httpx
import pytest
from httpx import ASGITransport

from api.routers import admin_transfers
from tests.conftest import _test_pool
from tests.fixtures.users import seed_session

_ORIGIN = {"Origin": "http://test"}


@pytest.fixture
async def client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


async def _agency(aconn, name: str, route_id: str, stops: list[str]) -> int:
    agency_id = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id", name, f"http://{name}/rt"
    )
    await aconn.execute("INSERT INTO static_routes (agency_id, route_id) VALUES ($1, $2)", agency_id, route_id)
    await aconn.executemany(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES ($1, $2, 'hub')",
        [(agency_id, s) for s in stops],
    )
    return agency_id


@pytest.fixture
async def agencies(aconn):
    bus = await _agency(aconn, "bus", "550332996", ["50271 0", "50271 1"])
    tram = await _agency(aconn, "tram", "2136956995", ["50271 1"])
    return bus, tram


def _full(bus: int, tram: int, **overrides) -> dict:
    """A replacement body: every field, as a PATCH needs it."""
    full = {"from_direction": None, "to_direction": None, "walk_sec": 120, "day_types": ["weekday"], "active": True}
    return _body(bus, tram, **{**full, **overrides})


def _body(bus: int, tram: int, **overrides) -> dict:
    body = {
        "name": "温品四丁目 29 → 2",
        "from_agency_id": bus,
        "from_route_code": "550332996",
        "from_stop_ids": ["50271 0"],
        "to_agency_id": tram,
        "to_route_code": "2136956995",
        "to_stop_ids": ["50271 1"],
    }
    return {**body, **overrides}


async def _audit(aconn) -> list[dict]:
    rows = await aconn.fetch("SELECT action, target_type, target_id, before, after FROM admin_audit ORDER BY id")
    return [
        {**dict(r), "before": r["before"] and json.loads(r["before"]), "after": r["after"] and json.loads(r["after"])}
        for r in rows
    ]


@pytest.mark.asyncio
async def test_a_non_admin_cannot_create_a_pair(client, aconn, agencies):
    sid = (await seed_session(aconn, role="user")).sid
    r = await client.post("/api/admin/transfer-pairs", json=_body(*agencies), cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 0


@pytest.mark.asyncio
async def test_an_anonymous_caller_cannot_create_a_pair(client, aconn, agencies):
    r = await client.post("/api/admin/transfer-pairs", json=_body(*agencies), headers=_ORIGIN)
    assert r.status_code == 401
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 0


@pytest.mark.asyncio
async def test_a_cross_site_create_is_refused(client, aconn, agencies):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await client.post(
        "/api/admin/transfer-pairs",
        json=_body(*agencies),
        cookies={"sid": sid},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 0


@pytest.mark.asyncio
async def test_create_update_delete_each_write_an_audit_row(client, aconn, agencies):
    sid = (await seed_session(aconn, role="admin")).sid
    created = await client.post(
        "/api/admin/transfer-pairs", json=_body(*agencies), cookies={"sid": sid}, headers=_ORIGIN
    )
    assert created.status_code == 201, created.text
    pair = created.json()
    assert pair["walk_sec"] == 120
    assert pair["day_types"] == ["weekday", "saturday", "sunday_holiday"]
    assert pair["from_direction"] is None

    updated = await client.patch(
        f"/api/admin/transfer-pairs/{pair['id']}",
        json=_full(*agencies, walk_sec=180, day_types=["sunday_holiday", "weekday"]),
        cookies={"sid": sid},
        headers=_ORIGIN,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["walk_sec"] == 180
    assert updated.json()["day_types"] == ["weekday", "sunday_holiday"]

    deleted = await client.delete(f"/api/admin/transfer-pairs/{pair['id']}", cookies={"sid": sid}, headers=_ORIGIN)
    assert deleted.status_code == 204
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 0

    audit = await _audit(aconn)
    assert [(a["action"], a["target_type"], a["target_id"]) for a in audit] == [
        ("transfer_pair.create", "transfer_pair", str(pair["id"])),
        ("transfer_pair.update", "transfer_pair", str(pair["id"])),
        ("transfer_pair.delete", "transfer_pair", str(pair["id"])),
    ]
    assert audit[0]["before"] is None and audit[0]["after"]["walk_sec"] == 120
    assert audit[1]["before"]["walk_sec"] == 120 and audit[1]["after"]["walk_sec"] == 180
    assert audit[2]["before"]["walk_sec"] == 180 and audit[2]["after"] is None


@pytest.mark.asyncio
async def test_the_list_is_admin_only_and_holds_inactive_pairs(client, aconn, agencies):
    admin = (await seed_session(aconn, role="admin")).sid
    r = await client.post(
        "/api/admin/transfer-pairs",
        json=_body(*agencies, active=False),
        cookies={"sid": admin},
        headers=_ORIGIN,
    )
    assert r.status_code == 201
    listed = await client.get("/api/admin/transfer-pairs", cookies={"sid": admin})
    assert [p["active"] for p in listed.json()] == [False]
    member = (await seed_session(aconn, role="user")).sid
    assert (await client.get("/api/admin/transfer-pairs", cookies={"sid": member})).status_code == 403


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"from_agency_id": 999999}, "from_agency_id"),
        ({"to_route_code": "nope"}, "to_route_code"),
        ({"from_stop_ids": ["50271 0", "missing"]}, "from_stop_ids"),
        ({"to_stop_ids": ["50271 0"]}, "to_stop_ids"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_agencies_routes_and_stops_are_refused(client, aconn, agencies, overrides, field):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await client.post(
        "/api/admin/transfer-pairs", json=_body(*agencies, **overrides), cookies={"sid": sid}, headers=_ORIGIN
    )
    assert r.status_code == 422
    assert field in r.json()["detail"]
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 0
    assert await _audit(aconn) == []


@pytest.mark.asyncio
async def test_a_deleted_agency_is_refused(client, aconn, agencies):
    bus, tram = agencies
    await aconn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = $1", tram)
    sid = (await seed_session(aconn, role="admin")).sid
    r = await client.post("/api/admin/transfer-pairs", json=_body(bus, tram), cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_the_number_of_pairs_is_capped(client, aconn, agencies, monkeypatch):
    monkeypatch.setattr(admin_transfers, "MAX_TRANSFER_PAIRS", 2)
    sid = (await seed_session(aconn, role="admin")).sid
    codes = []
    for _ in range(3):
        r = await client.post("/api/admin/transfer-pairs", json=_body(*agencies), cookies={"sid": sid}, headers=_ORIGIN)
        codes.append(r.status_code)
    assert codes == [201, 201, 409]
    assert await aconn.fetchval("SELECT count(*) FROM transfer_pairs") == 2


@pytest.mark.asyncio
async def test_missing_pairs_are_404_and_unaudited(client, aconn, agencies):
    sid = (await seed_session(aconn, role="admin")).sid
    put = await client.patch(
        "/api/admin/transfer-pairs/999", json=_full(*agencies), cookies={"sid": sid}, headers=_ORIGIN
    )
    delete = await client.delete("/api/admin/transfer-pairs/999", cookies={"sid": sid}, headers=_ORIGIN)
    assert (put.status_code, delete.status_code) == (404, 404)
    assert await _audit(aconn) == []


@pytest.mark.asyncio
async def test_a_replacement_leaving_a_field_out_is_refused_not_reset(client, aconn, agencies):
    sid = (await seed_session(aconn, role="admin")).sid
    created = await client.post(
        "/api/admin/transfer-pairs", json=_body(*agencies, active=False), cookies={"sid": sid}, headers=_ORIGIN
    )
    pair_id = created.json()["id"]
    full = _full(*agencies, active=False)
    del full["active"]
    r = await client.patch(f"/api/admin/transfer-pairs/{pair_id}", json=full, cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 422
    assert await aconn.fetchval("SELECT active FROM transfer_pairs WHERE id = $1", pair_id) is False
