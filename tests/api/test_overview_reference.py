"""Pulse's trailing-90-day reference: the headline's own figure over the 90 days
before the period, under the same scope, or a stated reason it has none."""

from datetime import date, datetime, timedelta

import pytest

from api.range import RangeCtx
from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from tests.api.test_overview import _seed_agg_daily
from tests.conftest import _test_pool
from tests.pipeline.ch_agg_helpers import update_row

# A 7-day period whose reference window is 2026-02-17..2026-05-17.
PERIOD = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
REF_FROM = date(2026, 2, 17)
REF_TO = date(2026, 5, 17)


def _days(start: date, end: date):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def _seed_reference_window(aconn, agency_id, *, route="R1", avg_min=2.0, samples=10, days=None):
    for day in days if days is not None else _days(REF_FROM, REF_TO):
        await _seed_agg_daily(aconn, agency_id, day, route, "平日", avg_min, samples)


async def _summary(aconn, agency_id, ctx, **kw):
    from pipeline.reports import compute_overview_summary

    return await compute_overview_summary(agency_id, ctx, aconn, **kw)


@pytest.mark.asyncio
async def test_the_reference_is_the_headline_over_the_90_days_before_the_period(aconn, aagency_id):
    """Two routes pool by their samples, as the headline does. The day before
    the window and the period's own days sit outside it."""
    await _seed_reference_window(aconn, aagency_id, route="R1", avg_min=2.0, samples=10)
    await _seed_reference_window(aconn, aagency_id, route="R2", avg_min=5.0, samples=30)
    await _seed_agg_daily(aconn, aagency_id, REF_FROM - timedelta(days=1), "R1", "平日", 40.0, 500)
    for day in _days(PERIOD.from_date, PERIOD.to_date):
        await _seed_agg_daily(aconn, aagency_id, day, "R1", "平日", 6.0, 40)

    out = await _summary(aconn, aagency_id, PERIOD)
    ref = out["headline"]["reference"]
    assert (ref["window_from"], ref["window_to"], ref["days"]) == ("2026-02-17", "2026-05-17", 90)
    assert (ref["days_selected"], ref["days_collected"], ref["min_days"]) == (90, 90, 72)
    assert ref["reason"] is None
    assert ref["samples"] == 90 * 40
    assert ref["avg_min"] == pytest.approx(4.25, abs=0.001)
    assert ref["delta_min"] == pytest.approx(6.0 - 4.25, abs=0.001)
    assert ref["delta_pct"] == pytest.approx(round((6.0 - 4.25) / 4.25 * 100, 1), abs=0.05)
    assert ref["min_samples"] == 100

    # Parity: the reference is what Pulse's headline prints for that window.
    window = await _summary(aconn, aagency_id, RangeCtx(from_date=REF_FROM, to_date=REF_TO))
    assert (ref["avg_min"], ref["samples"]) == (window["headline"]["avg_min"], window["headline"]["samples"])


@pytest.mark.asyncio
async def test_the_reference_keeps_the_scopes_routes_and_days_of_the_week(aconn, aagency_id, ch_async_client):
    """A routes and day-of-week scope narrows the reference exactly as it
    narrows the headline: R2 and the weekend days stay out of both."""
    for day in _days(REF_FROM, REF_TO):
        weekend = day.isoweekday() >= 6
        await _seed_agg_daily(aconn, aagency_id, day, "R1", "平日", 9.0 if weekend else 2.5, 20)
        await _seed_agg_daily(aconn, aagency_id, day, "R2", "平日", 30.0, 20)
    for day in _days(PERIOD.from_date, PERIOD.to_date):
        await _seed_agg_daily(aconn, aagency_id, day, "R1", "平日", 3.0, 20)

    scope = {"dow": "weekday", "routes": ("R1",)}
    period = RangeCtx(from_date=PERIOD.from_date, to_date=PERIOD.to_date, **scope)
    out = await _summary(aconn, aagency_id, period, ch=ch_async_client)
    ref = out["headline"]["reference"]
    assert ref["avg_min"] == pytest.approx(2.5, abs=0.001)
    assert (ref["days_selected"], ref["days_collected"], ref["samples"]) == (64, 64, 64 * 20)
    assert ref["reason"] is None

    window = await _summary(
        aconn, aagency_id, RangeCtx(from_date=REF_FROM, to_date=REF_TO, **scope), ch=ch_async_client
    )
    assert (ref["avg_min"], ref["samples"]) == (window["headline"]["avg_min"], window["headline"]["samples"])


@pytest.mark.asyncio
async def test_a_service_filter_is_resolved_over_the_reference_window(aconn, aagency_id):
    """The 平日 filter reads the reference window's own day types: its
    Sundays and its weekday public holidays (2/23, 3/20, 4/29, 5/4-5/6) are
    filed under 日祝 and stay out."""
    holidays = {date(2026, 2, 23), date(2026, 3, 20), date(2026, 4, 29), date(2026, 5, 4), date(2026, 5, 5)}
    holidays.add(date(2026, 5, 6))
    for day in _days(REF_FROM, REF_TO):
        off = day.isoweekday() == 7 or day in holidays
        await _seed_agg_daily(aconn, aagency_id, day, "R1", "", 12.0 if off else 1.5, 20)

    ctx = RangeCtx(from_date=PERIOD.from_date, to_date=PERIOD.to_date, service="平日")
    ref = (await _summary(aconn, aagency_id, ctx))["headline"]["reference"]
    assert ref["avg_min"] == pytest.approx(1.5, abs=0.001)
    assert ref["days_selected"] == ref["days_collected"] == 64 - 6
    assert ref["reason"] is None


@pytest.mark.asyncio
async def test_no_observations_in_the_window_is_no_data(aconn, aagency_id):
    for day in _days(PERIOD.from_date, PERIOD.to_date):
        await _seed_agg_daily(aconn, aagency_id, day, "R1", "平日", 3.0, 40)

    out = await _summary(aconn, aagency_id, PERIOD)
    ref = out["headline"]["reference"]
    assert ref["reason"] == "no_data"
    assert (ref["avg_min"], ref["delta_min"], ref["delta_pct"]) == (None, None, None)
    assert (ref["samples"], ref["days_collected"], ref["days_selected"]) == (0, 0, 90)
    assert (ref["window_from"], ref["window_to"]) == ("2026-02-17", "2026-05-17")


@pytest.mark.parametrize(("collected", "reason"), [(71, "insufficient_days"), (72, None)])
@pytest.mark.asyncio
async def test_a_reference_needs_data_on_four_fifths_of_the_days_it_selects(aconn, aagency_id, collected, reason):
    """A new agency's window, or one with gaps, holds data on fewer days: under
    72 of 90 the figure is withheld rather than shown as the agency's usual."""
    await _seed_reference_window(
        aconn, aagency_id, samples=50, days=_days(REF_TO - timedelta(days=collected - 1), REF_TO)
    )

    ref = (await _summary(aconn, aagency_id, PERIOD))["headline"]["reference"]
    assert (ref["days_collected"], ref["min_days"], ref["reason"]) == (collected, 72, reason)
    assert (ref["avg_min"] is None) == (reason is not None)
    assert ref["samples"] == collected * 50


@pytest.mark.asyncio
async def test_coverage_counts_the_days_the_agency_was_collected_not_the_days_a_route_ran(
    aconn, aagency_id, ch_async_client
):
    """A route that runs on fewer days than the agency is still judged by the
    agency's collected days; its reference averages the days it ran."""
    await _seed_reference_window(aconn, aagency_id, route="R_ALL", samples=10)
    await _seed_reference_window(
        aconn, aagency_id, route="R_SOME", avg_min=4.0, samples=10, days=_days(REF_FROM, REF_FROM + timedelta(days=29))
    )

    ctx = RangeCtx(from_date=PERIOD.from_date, to_date=PERIOD.to_date, routes=("R_SOME",))
    ref = (await _summary(aconn, aagency_id, ctx, ch=ch_async_client))["headline"]["reference"]
    assert (ref["days_collected"], ref["reason"], ref["samples"]) == (90, None, 300)
    assert ref["avg_min"] == pytest.approx(4.0, abs=0.001)


@pytest.mark.asyncio
async def test_fewer_than_100_observations_is_insufficient_samples(aconn, aagency_id):
    await _seed_reference_window(aconn, aagency_id, samples=1)

    ref = (await _summary(aconn, aagency_id, PERIOD))["headline"]["reference"]
    assert (ref["reason"], ref["samples"], ref["avg_min"]) == ("insufficient_samples", 90, None)


@pytest.mark.asyncio
async def test_a_time_band_with_routes_has_no_aggregate_to_read(aconn, aagency_id, ch_async_client):
    """No aggregate holds a route's departures by date and hour, so the
    reference says so instead of scanning ``updates`` over 90 days."""
    await _seed_reference_window(aconn, aagency_id)
    ctx = RangeCtx(from_date=PERIOD.from_date, to_date=PERIOD.to_date, time_band="morning", routes=("R1",))
    ref = (await _summary(aconn, aagency_id, ctx, ch=ch_async_client))["headline"]["reference"]
    assert (ref["reason"], ref["avg_min"]) == ("not_aggregated", None)
    assert (ref["window_from"], ref["window_to"]) == ("2026-02-17", "2026-05-17")


@pytest.mark.parametrize("band", ["morning", "late_night"])
@pytest.mark.asyncio
async def test_a_time_band_reference_matches_the_live_headline_over_its_window(
    pg_conn, agency_id, aconn, ch_client, ch_async_client, band
):
    """Under a time band the reference reads ``agg_hour_daily`` and the
    headline the live scan; analyze builds the one from the rows the other
    reads, so both give the same figure for the same window. A departure at
    24:30 on the service day's clock is 00:30, in the late-night band."""
    rows = []
    for i, day in enumerate(_days(REF_FROM, REF_TO)):
        stamp = datetime.combine(day, datetime.min.time())
        rows += [
            update_row(stamp.replace(hour=7, minute=5), f"m{i}", 60 + (i % 7) * 30, scheduled_time="07:00:00"),
            update_row(stamp.replace(hour=8, minute=15), f"n{i}", 400, scheduled_time="08:10:00", route_code="R2"),
            update_row(stamp.replace(hour=13, minute=5), f"d{i}", 900, scheduled_time="13:00:00"),
            update_row(stamp + timedelta(hours=24, minutes=40), f"l{i}", 120 + (i % 5) * 60, scheduled_time="24:30:00"),
            update_row(stamp.replace(hour=4, minute=40), f"e{i}", 30, scheduled_time="04:30:00"),
        ]
    insert_updates(ch_client, agency_id, rows)
    analyze(agency_id, pg_conn, ch_client)

    ctx = RangeCtx(from_date=PERIOD.from_date, to_date=PERIOD.to_date, time_band=band)
    ref = (await _summary(aconn, agency_id, ctx, ch=ch_async_client))["headline"]["reference"]
    window = await _summary(
        aconn, agency_id, RangeCtx(from_date=REF_FROM, to_date=REF_TO, time_band=band), ch=ch_async_client
    )
    assert ref["reason"] is None
    assert ref["samples"] == window["headline"]["samples"] > 0
    assert ref["avg_min"] == window["headline"]["avg_min"]


@pytest.mark.asyncio
async def test_the_pool_path_gives_the_same_reference(aconn, aagency_id):
    await _seed_reference_window(aconn, aagency_id, samples=10)
    seq = await _summary(aconn, aagency_id, PERIOD)
    pool = await _test_pool()
    try:
        pooled = await _summary(aconn, aagency_id, PERIOD, pool=pool)
    finally:
        await pool.close()
    assert pooled["headline"]["reference"] == seq["headline"]["reference"]
    assert seq["headline"]["reference"]["avg_min"] == pytest.approx(2.0, abs=0.001)


@pytest.mark.asyncio
async def test_the_endpoint_returns_the_reference(client, aconn, aagency_id):
    from api.main import app

    app.state.ch_client = None
    await _seed_reference_window(aconn, aagency_id, samples=10)
    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2026-05-18&to=2026-05-24")
    assert r.status_code == 200
    ref = r.json()["headline"]["reference"]
    assert ref == {
        "avg_min": 2.0,
        "delta_min": None,
        "delta_pct": None,
        "samples": 900,
        "window_from": "2026-02-17",
        "window_to": "2026-05-17",
        "days": 90,
        "days_selected": 90,
        "days_collected": 90,
        "min_days": 72,
        "min_samples": 100,
        "reason": None,
    }
