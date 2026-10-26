"""The coverage renewal is recorded as its own run kind, and every kind the
run helpers accept is one the table accepts too."""

import asyncpg
import pytest

from pipeline.runs import RUN_KINDS


@pytest.mark.asyncio
async def test_pipeline_runs_accepts_every_kind_the_helpers_write(aconn, aagency_id):
    for kind in RUN_KINDS:
        run_id = await aconn.fetchval(
            "INSERT INTO pipeline_runs (kind, agency_id, status) VALUES ($1, $2, 'running') RETURNING run_id",
            kind,
            aagency_id,
        )
        assert run_id is not None, kind


@pytest.mark.asyncio
async def test_pipeline_runs_still_rejects_an_unknown_kind(aconn):
    with pytest.raises(asyncpg.CheckViolationError):
        await aconn.execute("INSERT INTO pipeline_runs (kind, status) VALUES ('probes', 'running')")
