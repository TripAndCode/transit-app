"""API tests for GET /api/{agency_id}/headway_quality (item 94).

Seeds `agg_route_headway`/`agg_route_headway_daily` directly via asyncpg
(like tests/api/test_forecast_overview_endpoint.py) rather than driving the
full ClickHouse-backed `analyze()` pipeline -- this endpoint only ever reads
those two Postgres aggregates, so exercising `pipeline.analyze`'s own
population of them is `tests/pipeline/test_analyze.py`'s job, not this
file's.
"""

import os
from datetime import date

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def headway_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Headway Quality Test Agency",
        "http://headway-quality-test.example.com",
    )
    aid = row["agency_id"]

    await pool.executemany(
        "INSERT INTO agg_route_headway "
        "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        " is_high_frequency, scheduled_wait_mean_sec) VALUES ($1,$2,$3,$4,$5,$6)",
        [
            # Perfectly regular 8-minute (480s) scheduled headway -> mean
            # wait = headway/2 = 240s (pipeline.headways.mean_wait_from_moments).
            (aid, "R_EVEN", 480.0, 10, True, 240.0),
            (aid, "R_BUNCH", 480.0, 10, True, 240.0),
            # Classified NOT high-frequency -- must never appear below,
            # regardless of how much daily data it has.
            (aid, "R_SLOW", 1800.0, 10, False, 900.0),
            # High-frequency, but every agg_route_headway_daily row seeded
            # for it below falls outside the requested date range.
            (aid, "R_NODATA", 480.0, 10, True, 240.0),
            # High-frequency, in-range daily data exists, but that data
            # predates migration 0040's sufficient-statistics columns and
            # hasn't been re-`analyze()`-d yet (see below).
            (aid, "R_PREMIGRATE", 480.0, 10, True, 240.0),
        ],
    )
    await pool.executemany(
        "INSERT INTO agg_route_headway_daily "
        "(agency_id, route_code, date, actual_headway_median_sec, actual_samples, "
        " actual_headway_sum_sec, actual_headway_sumsq_sec2, long_gap_count) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            # R_EVEN: two identical-shape days of [480, 480] gaps -- pooled
            # mean wait stays 240s (== scheduled) -> EWT == 0, CoV == 0.
            (aid, "R_EVEN", date(2026, 4, 1), 480.0, 2, 960.0, 460800.0, 0),
            (aid, "R_EVEN", date(2026, 4, 2), 480.0, 2, 960.0, 460800.0, 0),
            # R_BUNCH: one day of [60, 900] gaps (same total as two scheduled
            # 480s headways) -- bunching raises the pooled mean wait above
            # scheduled -> EWT > 0; the 900s gap exceeds 1.75*480=840
            # -> long_gap_rate == 0.5.
            (aid, "R_BUNCH", date(2026, 4, 1), 480.0, 2, 960.0, 813600.0, 1),
            # R_SLOW has real daily data too, but is_high_frequency=False.
            (aid, "R_SLOW", date(2026, 4, 1), 1800.0, 1, 1800.0, 3240000.0, 0),
            # R_NODATA's only row is well outside the [2026-04-01, 2026-04-02]
            # range the tests below request.
            (aid, "R_NODATA", date(2026, 5, 1), 480.0, 2, 960.0, 460800.0, 0),
            # R_PREMIGRATE: `actual_samples` (pre-existing column) is
            # populated, but the three migration-0040 sufficient-statistics
            # columns are still NULL -- the transitional state between
            # applying that migration and the next `make analyze-all` for
            # this agency. Must degrade to None metrics, not a 500.
            (aid, "R_PREMIGRATE", date(2026, 4, 1), 480.0, 2, None, None, None),
        ],
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_headway, agg_route_headway_daily CASCADE")
    await pool.close()


async def test_headway_quality_even_fixture_yields_zero_ewt_and_cov(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    even = by_route["R_EVEN"]
    assert even["ewt_sec"] == pytest.approx(0.0, abs=1e-6)
    assert even["cov"] == pytest.approx(0.0, abs=1e-6)
    assert even["long_gap_rate"] == pytest.approx(0.0, abs=1e-6)
    assert even["samples"] == 4


async def test_headway_quality_bunched_fixture_yields_positive_ewt_and_long_gap_rate(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    bunched = by_route["R_BUNCH"]
    assert bunched["ewt_sec"] == pytest.approx(183.75, abs=0.01)
    assert bunched["cov"] > 0
    assert bunched["long_gap_rate"] == pytest.approx(0.5, abs=1e-6)
    assert bunched["samples"] == 2


async def test_headway_quality_excludes_non_high_frequency_and_out_of_range_routes(headway_client):
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    codes = {row["route_code"] for row in r.json()["rows"]}

    assert "R_SLOW" not in codes  # never surfaced, however much daily data it has
    assert "R_NODATA" not in codes  # high-frequency, but no data in the requested range


async def test_headway_quality_premigration_daily_row_yields_none_metrics(headway_client):
    """A high-frequency route whose in-range daily row predates migration
    0040's sufficient-statistics columns (NULL sum/sumsq/long_gap_count,
    non-null `actual_samples`) must surface as a 200 with unresolved (None)
    metric fields, per `HeadwayQualityRow`'s documented contract -- not a
    500 from `TypeError`s in the pooled-statistics arithmetic.
    """
    client, aid = headway_client
    r = await client.get(f"/api/{aid}/headway_quality?from=2026-04-01&to=2026-04-02")
    assert r.status_code == 200
    by_route = {row["route_code"]: row for row in r.json()["rows"]}

    premigrate = by_route["R_PREMIGRATE"]
    assert premigrate["ewt_sec"] is None
    assert premigrate["cov"] is None
    assert premigrate["long_gap_rate"] is None
    assert premigrate["samples"] == 2


async def test_headway_quality_empty_agency_returns_empty_rows(headway_client):
    client, _aid = headway_client
    empty = await asyncpg.connect(DATABASE_URL)
    try:
        row = await empty.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
            "Headway Quality Empty Agency",
            "http://headway-quality-empty.example.com",
        )
        empty_aid = row["agency_id"]
    finally:
        await empty.close()

    r = await client.get(f"/api/{empty_aid}/headway_quality")
    assert r.status_code == 200
    assert r.json()["rows"] == []
