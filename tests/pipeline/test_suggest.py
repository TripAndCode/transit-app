"""Unit tests for the Insight Panel's rule chain (pipeline/reports/suggest.py)."""

import os
from datetime import datetime, time, timedelta

import psycopg2
import pytest

from api.range import jst_today
from pipeline.analyze import analyze
from pipeline.reports.suggest import compute_suggestion
from tests.conftest import mirror_updates_to_ch

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def suggest_agency(apply_schema, ch_client):
    import asyncpg

    pool = await asyncpg.create_pool(DATABASE_URL)
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Suggest Test Agency",
        "http://suggest-test.example.com",
    )
    agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_route_daily_dist, agg_stop_seq CASCADE"
        )
    await pool.close()


async def _seed(pool, agency_id, route_code, day, delays_sec):
    """One `updates` row per delay value, distinct trips so dedup keeps them all."""
    async with pool.acquire() as conn:
        for i, d in enumerate(delays_sec):
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                f"{route_code}-{day}-trip-{i}",
                route_code,
                "weekday",
                time(10, 0),
                1,
                d,
                datetime.fromisoformat(f"{day}T10:{i // 60:02d}:{i % 60:02d}"),
                f"test/{route_code}/{day}/{i}.pb",
            )


def _run_analyze(agency_id, ch_client):
    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_anomaly_today_wins_when_present(suggest_agency, ch_client):
    pool, agency_id = suggest_agency
    today = jst_today()
    baseline_day = (today - timedelta(days=3)).isoformat()
    # 30 samples/day so both `> 20 samples` gates (ranking + on-time) pass.
    await _seed(pool, agency_id, "R1", baseline_day, [60] * 30)  # baseline: 1 min avg
    await _seed(pool, agency_id, "R1", today.isoformat(), [300] * 30)  # today: 5 min avg -> 5x baseline
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        result = await compute_suggestion(agency_id, conn, ch_client)

    assert result is not None
    assert result["report_type"] == "trend"
    assert result["route_code"] == "R1"
    assert result["severity"] == "notable"


@pytest.mark.asyncio
async def test_trend_shift_wins_when_no_anomaly_today(suggest_agency, ch_client):
    pool, agency_id = suggest_agency
    today = jst_today()
    # No data "today" at all (simulates analyze() not having caught up yet)
    # -> rule 1 finds nothing. Seed a clear first-half/second-half split
    # across the trailing week so route_trend_shift's delta clears 2 min.
    for offset in range(6, 3, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R2", day, [60] * 25)  # first half: 1 min avg
    for offset in range(2, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R2", day, [300] * 25)  # second half: 5 min avg
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        result = await compute_suggestion(agency_id, conn, ch_client)

    assert result is not None
    assert result["report_type"] == "trend"
    assert result["route_code"] == "R2"


@pytest.mark.asyncio
async def test_falls_back_to_worst_on_time_route(suggest_agency, ch_client):
    pool, agency_id = suggest_agency
    today = jst_today()
    # Flat, unremarkable delay pattern all week for two routes -- no anomaly,
    # no trend shift -- but R4 has a much worse on-time rate than R3.
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R3", day, [30] * 25)  # on-time (<=60s)
        await _seed(pool, agency_id, "R4", day, [600] * 25)  # always late
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        result = await compute_suggestion(agency_id, conn, ch_client)

    assert result is not None
    assert result["report_type"] == "on_time"
    assert result["route_code"] == "R4"
    assert result["severity"] == "normal"


@pytest.mark.asyncio
async def test_exclude_skips_already_shown_candidate(suggest_agency, ch_client):
    pool, agency_id = suggest_agency
    today = jst_today()
    baseline_day = (today - timedelta(days=3)).isoformat()
    await _seed(pool, agency_id, "R1", baseline_day, [60] * 30)
    await _seed(pool, agency_id, "R1", today.isoformat(), [300] * 30)
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        result = await compute_suggestion(
            agency_id, conn, ch_client, exclude=frozenset({("trend", "R1")})
        )

    # R1's anomaly is excluded and nothing else qualifies -> falls through
    # every rule to the always-available on-time fallback, which has no
    # data seeded here, so the whole chain returns None.
    assert result is None
