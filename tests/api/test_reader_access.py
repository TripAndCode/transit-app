"""With SSO configured, a signed-out reader reaches every reader route, while
the admin routes still answer only an admin."""

import pytest

from tests.fixtures.users import seed_session


@pytest.mark.asyncio
async def test_a_signed_out_reader_lists_the_agencies(client, sso_on):
    r = await client.get("/api/agencies")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_a_signed_out_reader_opens_an_agency_report(client, aagency_id, sso_on, monkeypatch):
    from api.main import app

    # The whole-day overview reads agg_* tables only, so no ClickHouse client
    # is needed; get_ch's stand-in would answer 503 if one were touched.
    monkeypatch.setattr(app.state, "ch_client", None, raising=False)
    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2020-01-01&to=2020-01-07")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_an_admin_route_refuses_a_signed_out_caller(client, sso_on):
    r = await client.get("/api/admin/flags")
    assert r.status_code == 401
    assert r.json() == {"detail": "auth required"}


@pytest.mark.asyncio
async def test_an_admin_route_refuses_a_signed_in_reader(client, aconn, sso_on):
    sid = (await seed_session(aconn, role="user")).sid
    r = await client.get("/api/admin/flags", cookies={"sid": sid})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_route_answers_an_admin(client, aconn, sso_on):
    sid = (await seed_session(aconn, role="admin")).sid
    r = await client.get("/api/admin/flags", cookies={"sid": sid})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_the_account_routes_still_need_a_session(client, sso_on):
    assert (await client.get("/api/me")).json() is None
    assert (await client.get("/api/me/sessions")).status_code == 401


@pytest.mark.asyncio
async def test_config_carries_no_sign_in_requirement(client, sso_on):
    body = (await client.get("/api/config")).json()
    assert body["auth_enabled"] is True
    assert "login_required" not in body
