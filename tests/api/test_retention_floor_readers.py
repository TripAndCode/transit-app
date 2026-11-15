"""Every reader that scans `updates` live refuses a period starting before the
agency's retention floor, with a 422 naming the floor, rather than read the
dropped month as one with no data. A look-back that alone crosses the floor
degrades instead of refusing a period the agency still retains. The
aggregate-backed readers keep every date, and without a floor nothing
changes."""

from __future__ import annotations

from datetime import timedelta

import pytest

from api.range import RangeCtx, jst_today
from pipeline.clickhouse import insert_updates
from pipeline.live_filing import RANGE_NOT_RETAINED_CODE
from tests.api.test_delay_record import _reading
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
    assert body["detail"].startswith(f"Detailed readings before {FLOOR.isoformat()} are no longer kept")


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
async def test_the_overview_under_a_time_band_from_the_floor_is_served_without_its_baseline(client, dropped):
    """Its baseline is the same width immediately before the period, which
    lies before the floor: the baseline is left out, the period served."""
    resp = await client.get(f"/api/{dropped}/overview/summary?from={FLOOR}&to={FLOOR}&time_band=morning", headers=EN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["headline"]["baseline_avg_min"] is None
    assert body["movers"] is None


@pytest.mark.asyncio
async def test_the_overview_routes_filter_from_the_floor_is_served(client, dropped, pg_conn):
    """Under a routes filter alone only the hour profiles read live, over the
    period alone."""
    _seed_route(pg_conn, dropped)
    resp = await client.get(f"/api/{dropped}/overview/summary?from={FLOOR}&to={FLOOR}&routes=R1", headers=EN)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_the_movers_live_look_back_starts_at_the_floor_in_whole_weeks(
    aconn, dropped, ch_async_client, monkeypatch
):
    """A narrow banded period's four weeks of history reach before the floor:
    the weeks before it stay empty instead of refusing the payload."""
    from pipeline.reports import overview

    spans = []
    real = overview.ctx_filing

    async def recorded(conn, ch, agency_id, ctx, **kwargs):
        spans.append(ctx.from_date)
        return await real(conn, ch, agency_id, ctx, **kwargs)

    monkeypatch.setattr(overview, "ctx_filing", recorded)
    ctx = RangeCtx(from_date=FLOOR + timedelta(days=7), to_date=FLOOR + timedelta(days=13), time_band="morning")
    history = await overview._route_weekly_history(dropped, ["R1"], ctx, aconn, ch=ch_async_client)
    assert history == {"R1": [None, None, None, None]}
    assert spans == [FLOOR]


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
    assert resp.json()["detail"].startswith(f"{FLOOR.isoformat()} より前の詳細な観測データは保存期間を過ぎている")


@pytest.mark.asyncio
async def test_an_ask_tool_gives_its_refusal_for_a_range_before_the_floor(aconn, dropped, ch_async_client):
    from pipeline.query.tools import dispatch

    ctx = RangeCtx(from_date=BEFORE, to_date=FLOOR, time_band="morning")
    result = await dispatch("top_n", {"metric": "avg_delay"}, ctx, aconn, dropped, locale="en", ch=ch_async_client)
    assert result.kind == "empty"
    assert result.summary.startswith(f"Detailed readings before {FLOOR.isoformat()} are no longer kept")


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
    assert f"Detailed readings before {FLOOR.isoformat()} are no longer kept" in resp.text


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


def _observe(ch_client, agency_id: int, day, route: str = "R1", trip: str = "T1") -> None:
    insert_updates(ch_client, agency_id, [_reading(trip, 1, 60, "18:00:00", route=route, day=day.isoformat())])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/delay_record/trips?date={day}&route=R1",
        "/delay_record/stops?date={day}&route=R1&trip=T1",
        "/today/route/R1/trips?date={day}",
        "/today/route/R1/stop-profile",
    ],
)
async def test_a_one_day_reader_refuses_a_day_before_the_floor(client, dropped, pg_conn, ch_client, path):
    """The route's latest observation is a day before the floor, which
    `updates` can still hold rows of; the stop profile reads that day."""
    _seed_route(pg_conn, dropped)
    _observe(ch_client, dropped, BEFORE)
    resp = await client.get(f"/api/{dropped}" + path.format(day=BEFORE), headers=EN)
    _assert_not_retained(resp)


@pytest.mark.asyncio
async def test_a_stalled_agencys_route_with_nothing_retained_reads_as_unobserved(client, dropped, pg_conn, ch_client):
    """The probe's 30 days end at the agency's own newest reading, so they
    reach before the floor without the request naming any date."""
    _seed_route(pg_conn, dropped)
    _observe(ch_client, dropped, FLOOR + timedelta(days=5), route="R2")
    resp = await client.get(f"/api/{dropped}/today/route/R1/trips", headers=EN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["trips"] == []


@pytest.mark.asyncio
async def test_describe_data_starts_the_data_at_the_aggregates_and_names_the_floor(
    aconn, dropped, ch_client, ch_async_client
):
    from pipeline.query.tools import dispatch

    first_aggregated = FLOOR - timedelta(days=200)
    await aconn.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1, $2, 'R1', '平日', 0, 0, 1, 1, NOW(), 0)",
        dropped,
        first_aggregated,
    )
    newest = FLOOR + timedelta(days=2)
    _observe(ch_client, dropped, newest)
    ctx = RangeCtx(from_date=FLOOR, to_date=newest)
    result = await dispatch(
        "describe_data", {"kind": "date_range"}, ctx, aconn, dropped, locale="en", ch=ch_async_client
    )
    assert result.summary == (
        f"observation window: {first_aggregated} – {newest} (detailed readings kept from {FLOOR})"
    )
    assert ("raw_readings_kept_from", FLOOR.isoformat()) in result.pairs
    overview = await dispatch(
        "describe_data", {"kind": "overview"}, ctx, aconn, dropped, locale="en", ch=ch_async_client
    )
    assert ("first_aggregated_date", first_aggregated.isoformat()) in overview.pairs
    assert ("raw_readings_kept_from", FLOOR.isoformat()) in overview.pairs


@pytest.mark.asyncio
async def test_describe_data_without_a_floor_is_unchanged(aconn, agency_id, ch_client, ch_async_client):
    from pipeline.query.tools import dispatch

    _observe(ch_client, agency_id, FLOOR)
    ctx = RangeCtx(from_date=FLOOR, to_date=FLOOR)
    result = await dispatch(
        "describe_data", {"kind": "date_range"}, ctx, aconn, agency_id, locale="en", ch=ch_async_client
    )
    assert result.summary == f"observation window: {FLOOR} – {FLOOR}"
    assert not any(key == "raw_readings_kept_from" for key, _ in result.pairs)
