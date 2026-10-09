"""Promotion is recorded as its own run kind, so a failed copy is its own red bar."""

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_pipeline_runs_accepts_a_promote_run(aconn, aagency_id):
    run_id = await aconn.fetchval(
        "INSERT INTO pipeline_runs (kind, agency_id, status) VALUES ('promote', $1, 'running') RETURNING run_id",
        aagency_id,
    )
    assert run_id is not None


@pytest.mark.asyncio
async def test_pipeline_runs_still_rejects_an_unknown_kind(aconn):
    with pytest.raises(asyncpg.CheckViolationError):
        await aconn.execute("INSERT INTO pipeline_runs (kind, status) VALUES ('promotion', 'running')")
