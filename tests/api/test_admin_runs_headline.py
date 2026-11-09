"""A rebuild's headline before and after reaches the run timeline, and only an admin."""

import json

import httpx
import pytest
from httpx import ASGITransport

from api.admin_runs import INSERT_MANUAL_RUN_SQL, shape_run
from tests.conftest import _test_pool
from tests.fixtures.users import seed_session

_HEADLINE = {
    "first_day": "2026-06-08",
    "last_day": "2026-06-09",
    "preset": "legacy_60s",
    "method_version": "2026.11.3",
    "before": {"on_time_pct": 50.0, "avg_delay_min": 12.13, "samples": 8},
    "after": {"on_time_pct": 50.0, "avg_delay_min": 2.13, "samples": 8},
}


@pytest.fixture
async def admin_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


async def _rebuild_run(aconn, agency_id) -> int:
    return await aconn.fetchval(
        "INSERT INTO pipeline_runs (kind, agency_id, started_at, finished_at, status, rebuild_headline) "
        "VALUES ('analyze', $1, now(), now(), 'ok', $2::jsonb) RETURNING run_id",
        agency_id,
        json.dumps(_HEADLINE),
    )


@pytest.mark.asyncio
async def test_the_timeline_carries_a_rebuilds_headline_on_its_run(admin_client, aconn, aagency_id):
    run_id = await _rebuild_run(aconn, aagency_id)
    sid = (await seed_session(aconn, role="admin")).sid

    r = await admin_client.get("/api/admin/runs", cookies={"sid": sid})

    assert r.status_code == 200
    run = next(run for run in r.json()["runs"] if run["run_id"] == run_id)
    assert run["rebuild_headline"] == _HEADLINE


@pytest.mark.asyncio
async def test_a_rebuilds_headline_is_for_admins_only(admin_client, aconn, aagency_id):
    await _rebuild_run(aconn, aagency_id)
    sid = (await seed_session(aconn, role="user")).sid

    r = await admin_client.get("/api/admin/runs", cookies={"sid": sid})

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a_run_an_operator_just_opened_has_no_headline_yet(aconn):
    row = await aconn.fetchrow(INSERT_MANUAL_RUN_SQL, "analyze", None, None)
    assert shape_run(row)["rebuild_headline"] is None
