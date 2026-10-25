"""ml_evaluations holds one row per weekly evaluation run, keyed by run date:
the summary the VPS publishes, plus the columns a history list reads."""

import json
from datetime import date

import pytest


@pytest.mark.asyncio
async def test_the_table_keeps_one_row_per_run_date_and_upserts_a_republished_week(aconn):
    insert = (
        "INSERT INTO ml_evaluations (run_date, schema_version, adopted, overall_skill, summary) "
        "VALUES ($1, 1, $2, $3, $4::jsonb) "
        "ON CONFLICT (run_date) DO UPDATE SET adopted = EXCLUDED.adopted, "
        "overall_skill = EXCLUDED.overall_skill, summary = EXCLUDED.summary, ingested_at = now()"
    )
    week = date(2026, 10, 25)
    await aconn.execute(insert, week, False, 0.046, json.dumps({"adopted": False}))
    await aconn.execute(insert, week, True, 0.051, json.dumps({"adopted": True}))
    row = await aconn.fetchrow("SELECT adopted, overall_skill, summary FROM ml_evaluations WHERE run_date = $1", week)
    assert row["adopted"] is True
    assert row["overall_skill"] == pytest.approx(0.051)
    assert json.loads(row["summary"]) == {"adopted": True}
    assert await aconn.fetchval("SELECT count(*) FROM ml_evaluations") == 1


@pytest.mark.asyncio
async def test_an_unmeasured_skill_is_allowed(aconn):
    await aconn.execute(
        "INSERT INTO ml_evaluations (run_date, schema_version, adopted, overall_skill, summary) "
        "VALUES ($1, 1, false, NULL, '{}'::jsonb)",
        date(2026, 10, 18),
    )
    assert await aconn.fetchval("SELECT overall_skill FROM ml_evaluations") is None
