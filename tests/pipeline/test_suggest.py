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


async def _seed(pool, agency_id, route_code, day, delays_sec, service_type="weekday"):
    """One `updates` row per delay value, distinct trips so dedup keeps them all."""
    async with pool.acquire() as conn:
        for i, d in enumerate(delays_sec):
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                f"{route_code}-{service_type}-{day}-trip-{i}",
                route_code,
                service_type,
                time(10, 0),
                1,
                d,
                datetime.fromisoformat(f"{day}T10:{i // 60:02d}:{i % 60:02d}"),
                f"test/{route_code}/{service_type}/{day}/{i}.pb",
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
async def test_anomaly_fires_when_wall_clock_today_has_zero_rows(suggest_agency, ch_client):
    """analyze() normally lags the wall clock by >= 1 day, so wall-clock
    "today" has zero agg rows at the moment the Insight Panel is viewed --
    exactly the scenario that used to make rule 1 always fall through (see
    the module's rewrite: anchoring on jst_today() instead of the latest
    analyzed date). Seed data only through "yesterday" (nothing for
    jst_today() itself) and confirm the anomaly rule still fires, anchored
    on the latest analyzed date.
    """
    pool, agency_id = suggest_agency
    today = jst_today()
    yesterday = (today - timedelta(days=1)).isoformat()
    baseline_day = (today - timedelta(days=4)).isoformat()
    # Nothing seeded for `today` at all -- only through "yesterday".
    await _seed(pool, agency_id, "R1", baseline_day, [60] * 30)  # baseline: 1 min avg
    await _seed(pool, agency_id, "R1", yesterday, [300] * 30)  # "latest day": 5 min avg
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        # Sanity-check the premise: wall-clock "today" really has no agg row.
        latest = await conn.fetchval("SELECT MAX(date) FROM agg_route_daily_dist WHERE agency_id = $1", agency_id)
        assert latest is not None
        assert latest.isoformat() == yesterday
        assert latest != today

        result = await compute_suggestion(agency_id, conn, ch_client)

    assert result is not None
    assert result["report_type"] == "trend"
    assert result["route_code"] == "R1"
    assert result["severity"] == "notable"
    # The evaluation window must be anchored on the latest analyzed date
    # (yesterday), not the wall clock.
    assert result["from_date"] == yesterday
    assert result["to_date"] == yesterday


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
        result = await compute_suggestion(agency_id, conn, ch_client, exclude=frozenset({("trend", "R1")}))

    # R1's trend anomaly is excluded, but it's a distinct pathway so the
    # on-time fallback can still suggest it (the ("on_time", "R1") tuple
    # is not excluded, only ("trend", "R1") is). This demonstrates that
    # exclusion is per (report_type, route_code) pair, not blanket route.
    assert result is not None
    assert result["report_type"] == "on_time"
    assert result["route_code"] == "R1"
    assert result["severity"] == "normal"


@pytest.mark.asyncio
async def test_exclude_exact_tuple_matching_fallback(suggest_agency, ch_client):
    pool, agency_id = suggest_agency
    today = jst_today()
    # Flat, unremarkable delay patterns for two routes (no anomaly/trend signal)
    # -> both rules fall through, fallback picks the worst on-time route.
    # We then exclude that route's on-time tuple and verify the other is returned.
    for offset in range(6, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R5", day, [30] * 25)  # on-time (<=60s)
        await _seed(pool, agency_id, "R6", day, [600] * 25)  # always late -> worst
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        # Exclude R6's on-time fallback specifically
        result = await compute_suggestion(agency_id, conn, ch_client, exclude=frozenset({("on_time", "R6")}))

    # R6 is worst on-time but excluded -> fallback returns R5 instead
    assert result is not None
    assert result["report_type"] == "on_time"
    assert result["route_code"] == "R5"
    assert result["severity"] == "normal"


@pytest.mark.asyncio
async def test_on_time_fallback_pools_full_route_before_truncating(suggest_agency, ch_client, monkeypatch):
    """Regression test for the truncate-then-pool ordering bug: compute_on_time's
    fetch used to be sliced to ON_TIME_FALLBACK_FETCH_LIMIT *before*
    _pool_on_time_by_route ran, so a route whose service-type rows straddled
    the fetch boundary got pooled from a partial subset of its own rows.

    Three (route, service_type) rows this week, ascending by on_time_pct:
      RT/svcA    0%   (samples=30)
      R_other    30%  (samples=30)
      RT/svcB    100% (samples=30)
    True sample-weighted pooled RT = (0*30 + 100*30) / 60 = 50% -- WORSE
    on-time than R_other's 30%, so R_other is genuinely this week's
    worst-on-time ROUTE, at 30%.

    With a too-small fetch limit (2, monkeypatched below to reproduce the old
    bug's mechanism at a scale that doesn't need 50+ real rows), the fetch
    truncates to just [RT/svcA, R_other] before RT/svcB is ever pooled in:
    RT's pooled pct collapses to 0% (only svcA counted). The fallback then
    both picks the WRONG route (RT instead of R_other) and reports the WRONG
    percentage for it (0% instead of 50%) -- exactly the failure mode seen on
    real data with the old ``ON_TIME_FALLBACK_FETCH_LIMIT = 50`` once an
    agency had more than 50 qualifying rows. A limit that comfortably covers
    all 3 rows (the module's real, shipped value -- exercised unpatched
    below) must not reproduce that truncation, correctly reporting R_other
    at 30%. (The shipped constant's magnitude relative to real per-agency
    row counts is asserted directly, DB-free, in
    ``tests/unit/test_suggest_pooling.py``.)
    """
    from unittest.mock import patch

    from api.range import RangeCtx
    from pipeline.reports.suggest import _on_time_fallback

    pool, agency_id = suggest_agency
    day = jst_today().isoformat()
    await _seed(pool, agency_id, "RT", day, [600] * 30, service_type="svcA")  # 0% on-time
    await _seed(pool, agency_id, "RT", day, [30] * 30, service_type="svcB")  # 100% on-time
    await _seed(pool, agency_id, "R_other", day, [30] * 9 + [600] * 21, service_type="weekday")  # 9/30 = 30%
    _run_analyze(agency_id, ch_client)

    week_ctx = RangeCtx(from_date=jst_today(), to_date=jst_today())

    async with pool.acquire() as conn:
        # Reproduce the old bug's mechanism with a deliberately tiny fetch
        # limit -- this is what ON_TIME_FALLBACK_FETCH_LIMIT = 50 did on real
        # data once an agency had more than 50 qualifying rows.
        with patch("pipeline.reports.suggest.ON_TIME_FALLBACK_FETCH_LIMIT", 2):
            truncated = await _on_time_fallback(agency_id, conn, ch_client, week_ctx, frozenset(), "ja")
        assert truncated is not None
        assert truncated["route_code"] == "RT"
        assert "0%" in truncated["reason_text"]  # RT's pooled pct wrongly collapsed to 0%

        # The shipped limit (patched back to its real value on exiting the
        # `with` above) must not reproduce that truncation.
        result = await _on_time_fallback(agency_id, conn, ch_client, week_ctx, frozenset(), "ja")

    assert result is not None
    assert result["report_type"] == "on_time"
    assert result["route_code"] == "R_other"
    assert "30%" in result["reason_text"]
