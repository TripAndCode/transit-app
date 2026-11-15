"""Every reader that scans `updates` live refuses a range starting before the
agency's retention floor, with a 422 naming the floor, rather than read the
dropped month as one with no data. The aggregate-backed readers keep every
date, and without a floor nothing changes."""

from __future__ import annotations

from datetime import timedelta

import pytest

from api.range import RangeCtx, jst_today
from api.retention_guard import RANGE_NOT_RETAINED_CODE
from tests.conftest import TEST_ORIGIN

# A floor is the first of a month. These dates stay inside every reader's
# window: the 365-day range clamp, and the timeline's last 365 days.
FLOOR = (jst_today().replace(day=1) - timedelta(days=40)).replace(day=1)
BEFORE = FLOOR - timedelta(days=3)
EN = {"Accept-Language": "en"}


def _record_drop(pg_conn, agency_id: int, floor=FLOOR) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes, dropped_at) "
            "VALUES (%s, %s, %s, 10, 100, now())",
            ((floor - timedelta(days=1)).strftime("%Y%m"), [agency_id], floor),
        )
    pg_conn.commit()


@pytest.fixture
def floor_app(ch_async_client):
    from api.main import app

    app.state.ch_client = ch_async_client
    return app


@pytest.fixture
def dropped(pg_conn, agency_id, floor_app):
    _record_drop(pg_conn, agency_id)
    return agency_id


def _assert_not_retained(resp) -> None:
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == RANGE_NOT_RETAINED_CODE
    assert body["floor"] == FLOOR.isoformat()
    assert body["detail"].startswith(f"Raw readings before {FLOOR.isoformat()} are no longer retained")


def _seed_route(pg_conn, agency_id: int, route: str = "R1") -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
            "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
            "VALUES (%s, %s, %s, '平日', 0, 0, 1, 1, NOW(), 0)",
            (agency_id, FLOOR, route),
        )
    pg_conn.commit()


@pytest.mark.asyncio
async def test_the_delay_certificate_refuses_a_range_before_the_floor(client, dropped):
    resp = await client.get(
        f"/api/{dropped}/reports/delay_certificate?from={BEFORE}&to={FLOOR + timedelta(days=3)}", headers=EN
    )
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_a_banded_report_refuses_a_range_before_the_floor(client, dropped):
    resp = await client.get(f"/api/{dropped}/reports/ranking?from={BEFORE}&to={FLOOR}&time_band=morning", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_trend_heatmap_refuses_a_range_before_the_floor(client, dropped):
    resp = await client.get(f"/api/{dropped}/reports/trend?from={BEFORE}&to={FLOOR}&routes=R1", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_overview_under_a_time_band_refuses_a_range_before_the_floor(client, dropped):
    resp = await client.get(f"/api/{dropped}/overview/summary?from={BEFORE}&to={FLOOR}&time_band=morning", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_overview_under_a_time_band_refuses_a_period_starting_at_the_floor(client, dropped):
    """Its baseline is the same width immediately before the period, read
    from the same live grain, so it reaches before the floor."""
    resp = await client.get(f"/api/{dropped}/overview/summary?from={FLOOR}&to={FLOOR}&time_band=morning", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_overview_hour_profiles_under_a_routes_filter_refuse_a_range_before_the_floor(
    client, dropped, pg_conn
):
    """Only the hour profiles scan live under a routes filter alone; they are
    refused like every live read, not degraded as a ClickHouse failure."""
    _seed_route(pg_conn, dropped)
    resp = await client.get(f"/api/{dropped}/overview/summary?from={BEFORE}&to={FLOOR}&routes=R1", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_map_timeline_refuses_a_day_before_the_floor(client, dropped):
    resp = await client.get(f"/api/{dropped}/delays/timeline?date={BEFORE}", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_map_route_shape_refuses_a_range_before_the_floor(client, dropped, pg_conn):
    _seed_route(pg_conn, dropped)
    resp = await client.get(f"/api/{dropped}/route-shape?route=R1&from={BEFORE}&to={FLOOR}", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_replay_refuses_a_day_before_the_floor(client, dropped):
    resp = await client.get(f"/api/agencies/{dropped}/replay?date={BEFORE}", headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_the_refusal_is_localized(client, dropped):
    resp = await client.get(
        f"/api/{dropped}/reports/delay_certificate?from={BEFORE}&to={FLOOR}", headers={"Accept-Language": "ja"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith(f"{FLOOR.isoformat()} より前の生の観測データは保存期間を過ぎた")


@pytest.mark.asyncio
async def test_an_ask_tool_gives_its_refusal_for_a_range_before_the_floor(aconn, dropped, ch_async_client):
    from pipeline.query.tools import dispatch

    ctx = RangeCtx(from_date=BEFORE, to_date=FLOOR, time_band="morning")
    result = await dispatch("top_n", {"metric": "avg_delay"}, ctx, aconn, dropped, locale="en", ch=ch_async_client)
    assert result.kind == "empty"
    assert result.summary.startswith(f"Raw readings before {FLOOR.isoformat()} are no longer retained")


@pytest.mark.asyncio
async def test_an_ask_card_gives_the_refusal_as_its_answer(client, dropped):
    resp = await client.post(
        f"/api/{dropped}/ask/card",
        json={
            "tool": "top_n",
            "args": {"metric": "avg_delay"},
            "filter_ctx": {"from_date": BEFORE.isoformat(), "to_date": FLOOR.isoformat(), "time_band": "morning"},
        },
        headers={**EN, "Origin": TEST_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    assert f"Raw readings before {FLOOR.isoformat()} are no longer retained" in resp.text


@pytest.mark.asyncio
async def test_a_range_starting_at_the_floor_is_served(client, dropped):
    resp = await client.get(
        f"/api/{dropped}/reports/delay_certificate?from={FLOOR}&to={FLOOR + timedelta(days=3)}", headers=EN
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_a_banded_report_from_the_floor_on_is_served(client, dropped):
    resp = await client.get(f"/api/{dropped}/reports/ranking?from={FLOOR}&to={FLOOR}&time_band=morning", headers=EN)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_an_aggregate_backed_report_before_the_floor_is_served(client, dropped):
    resp = await client.get(f"/api/{dropped}/reports/ranking?from={BEFORE}&to={FLOOR}", headers=EN)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_without_a_floor_a_live_report_before_any_date_is_served(client, agency_id, floor_app):
    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={BEFORE}&to={FLOOR}", headers=EN)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_another_agencys_floor_leaves_this_one_served(client, agency_id, pg_conn, floor_app):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("他社", "http://example.com/b.pb"),
        )
        other = cur.fetchone()[0]
    pg_conn.commit()
    _record_drop(pg_conn, other)
    resp = await client.get(f"/api/{agency_id}/reports/delay_certificate?from={BEFORE}&to={FLOOR}", headers=EN)
    assert resp.status_code == 200, resp.text
