"""Unit tests for the Insight Panel's rule chain (pipeline/reports/suggest.py)."""

import os
from datetime import datetime, time, timedelta

import psycopg2
import pytest

from api.range import jst_today
from pipeline.analyze import analyze
from pipeline.db import DEFAULT_DATABASE_URL
from pipeline.reports.suggest import compute_suggestion
from tests.conftest import _test_pool, mirror_updates_to_ch

DATABASE_URL = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


@pytest.fixture
async def suggest_agency(apply_schema, ch_client):

    pool = await _test_pool()
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Suggest Test Agency",
        "http://suggest-test.example.com",
    )
    agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, agg_route_stats, agg_route_hour, agg_daily_trend, agg_route_daily_dist CASCADE"
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
    # The card names the route; the text names the day it judged.
    assert result["reason_text"].startswith(f"{today.month}/{today.day}（")


@pytest.mark.asyncio
async def test_reason_text_dates_the_day_it_judged_and_leaves_the_route_to_the_card(suggest_agency, ch_client):
    """The rule judges the agency's latest analyzed day, which lags the wall
    clock, so the text names that date rather than calling it today. The
    route's name is the card's own label, which tells apart routes sharing a
    number, so the text carries neither the name nor the code."""
    pool, agency_id = suggest_agency
    today = jst_today()
    judged = today - timedelta(days=2)
    await _seed(pool, agency_id, "47011", (judged - timedelta(days=3)).isoformat(), [60] * 30)
    await _seed(pool, agency_id, "47011", judged.isoformat(), [300] * 30)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, $2, $3)",
            agency_id,
            "沖舘・新田線(47011)",
            "W54 沖舘・新田線",
        )
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        result = await compute_suggestion(agency_id, conn, ch_client, locale="en")

    assert result is not None and result["route_code"] == "47011"
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[judged.weekday()]
    assert result["reason_text"].startswith(f"Average delay on {weekday} {judged.month}/{judged.day} was much worse")
    assert result["to_date"] == judged.isoformat()
    assert "today" not in result["reason_text"]
    assert "47011" not in result["reason_text"]
    assert "W54" not in result["reason_text"]


@pytest.mark.asyncio
async def test_anomaly_fires_when_wall_clock_today_has_zero_rows(suggest_agency, ch_client):
    """analyze() normally lags the wall clock by >= 1 day, so wall-clock
    "today" has zero agg rows at the moment the Insight Panel is viewed --
    exactly the scenario in which anchoring on jst_today() instead of the
    latest analyzed date would make rule 1 always fall through. Seed data
    only through "yesterday" (nothing for jst_today() itself) and confirm the
    anomaly rule still fires, anchored on the latest analyzed date.
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
@pytest.mark.parametrize(
    ("first_half_sec", "second_half_sec", "expected_ja", "unexpected_ja"),
    [(60, 300, "悪化", "改善"), (300, 60, "改善", "悪化")],
)
async def test_trend_shift_reason_names_the_direction_of_the_shift(
    suggest_agency, ch_client, first_half_sec, second_half_sec, expected_ja, unexpected_ja
):
    pool, agency_id = suggest_agency
    today = jst_today()
    for offset in range(6, 3, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R2", day, [first_half_sec] * 25)
    for offset in range(2, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        await _seed(pool, agency_id, "R2", day, [second_half_sec] * 25)
    _run_analyze(agency_id, ch_client)

    async with pool.acquire() as conn:
        ja = await compute_suggestion(agency_id, conn, ch_client, locale="ja")
        en = await compute_suggestion(agency_id, conn, ch_client, locale="en")

    assert ja is not None and en is not None
    assert ja["report_type"] == "trend"
    assert expected_ja in ja["reason_text"]
    assert unexpected_ja not in ja["reason_text"]
    english = "worsened" if expected_ja == "悪化" else "improved"
    assert english in en["reason_text"]


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
async def test_on_time_fallback_judges_a_route_on_all_its_observations(suggest_agency, ch_client):
    """compute_on_time returns one row per route, so the fallback names each
    route at its on-time rate over every observation, whichever raw service
    ids they matched, and no fetch size can split a route.

    RT's observations split across two raw service ids (0% and 100% on time,
    30 each) pool to 50%, which beats R_other's 30%: R_other is this week's
    worst on-time route, even when the fetch keeps a single row.
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
        for limit in (1, 5000):
            with patch("pipeline.reports.suggest.ON_TIME_FALLBACK_FETCH_LIMIT", limit):
                result = await _on_time_fallback(agency_id, conn, ch_client, week_ctx, frozenset(), "ja")
            assert result is not None
            assert result["report_type"] == "on_time"
            assert result["route_code"] == "R_other"
            assert "30%" in result["reason_text"]
