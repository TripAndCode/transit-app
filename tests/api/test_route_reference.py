"""Each route's trailing-90-day reference on the Routes table and the route
page: the route's own on-time rate and average delay over the 90 days before
the period, under the table's scope and on-time window, or a stated reason it
has none."""

import csv
import io
from datetime import date, timedelta

import pytest

from api.middleware.ratelimit import limiter
from tests.api.test_overview import _seed_agg_daily
from tests.api.test_reports import (  # noqa: F401 -- reports_app/reports_client are fixtures
    _load_for_path,
    _seed_route_at,
    reports_app,
    reports_client,
)
from tests.fixtures.route_dist import seed_route_dist_day

PERIOD_FROM = date(2026, 5, 18)
PERIOD_TO = date(2026, 5, 24)
REF_FROM = date(2026, 2, 17)
REF_TO = date(2026, 5, 17)

# A route on time six departures in ten under every window the tests name,
# and one whose early and late departures move with the window.
STEADY = [0] * 60 + [400] * 40
MIXED = [-120] * 10 + [-45] * 10 + [0] * 50 + [90] * 10 + [200] * 10 + [400] * 10
# The period's own departures: never all exactly 0 s, which would leave a
# route out of the lists as one whose feed reports no delay.
PERIOD = [30] * 100


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def _seed(pool, agency_id, route, delays, days):
    """``delays`` on each of ``days`` for ``route``, in the per-route table the
    figures read and the daily table coverage reads."""
    for day in days:
        await seed_route_dist_day(pool, agency_id, route, day, delays)
        await _seed_agg_daily(pool, agency_id, day, route, "", sum(delays) / len(delays) / 60, len(delays))


async def _report(client, agency_id, report, start, end, query=""):
    resp = await client.get(f"/api/{agency_id}/reports/{report}?from={start}&to={end}{query}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _window_rows(client, agency_id, query=""):
    """The Routes table itself over the reference window, every route listed."""
    body = await _report(client, agency_id, "route_table", REF_FROM, REF_TO, f"&include_sparse=1{query}")
    return {r[0]: r for r in body["rows"]}


@pytest.mark.asyncio
async def test_each_routes_reference_is_its_own_table_row_over_the_90_days_before(reports_client):  # noqa: F811
    """The reference is the table's own computation over the window: the day
    before it and the period's own days stay out. The on-time list the route
    page reads carries the same reference."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(REF_FROM, REF_TO))
    await _seed(pool, agency_id, "B", MIXED, _days(REF_FROM, REF_TO))
    await _seed(pool, agency_id, "A", [2000] * 100, [REF_FROM - timedelta(days=1)])
    await _seed(pool, agency_id, "B", PERIOD, _days(PERIOD_FROM, PERIOD_TO))
    await _seed(pool, agency_id, "A", PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    body = await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO)
    ref = body["reference"]
    assert (ref["window_from"], ref["window_to"], ref["days"]) == ("2026-02-17", "2026-05-17", 90)
    assert (ref["days_selected"], ref["days_collected"], ref["min_days"], ref["min_samples"]) == (90, 90, 72, 100)
    assert (ref["reason"], ref["samples"]) == (None, 90 * 200)

    window = await _window_rows(client, agency_id)
    assert set(ref["routes"]) == {"A", "B"}
    for code, entry in ref["routes"].items():
        row = window[code]
        assert entry == {
            "on_time_pct": float(row[2]),
            "avg_min": float(row[1]),
            "samples": row[5],
            "reason": None,
        }
    assert ref["routes"]["A"]["on_time_pct"] == 60.0

    on_time = await _report(client, agency_id, "on_time", PERIOD_FROM, PERIOD_TO)
    assert on_time["reference"] == ref


@pytest.mark.parametrize("window", ["", "&late=60", "&preset=legacy_60s", "&early=any", "&late=120", "&early=30"])
@pytest.mark.asyncio
async def test_the_reference_counts_on_time_under_the_tables_own_window(reports_client, window):  # noqa: F811
    """Both presets, a tolerance the exact columns answer and ones only the
    merged histogram does: the reference reads each as the table does."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "B", MIXED, _days(REF_FROM, REF_TO))
    await _seed(pool, agency_id, "B", PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    entry = (await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO, window))["reference"]["routes"]
    row = (await _window_rows(client, agency_id, window))["B"]
    assert entry["B"]["on_time_pct"] == float(row[2])
    assert entry["B"]["reason"] is None


@pytest.mark.asyncio
async def test_the_window_moves_the_reference_with_the_on_time_rate(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "B", MIXED, _days(REF_FROM, REF_TO))
    await _seed(pool, agency_id, "B", PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    def rate(body):
        return body["reference"]["routes"]["B"]["on_time_pct"]

    default = await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO)
    legacy = await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO, "&preset=legacy_60s")
    assert (rate(default), rate(legacy)) == (80.0, 70.0)


@pytest.mark.asyncio
async def test_the_reference_names_only_the_routes_the_response_lists(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    for code, late in (("A", 400), ("B", 60)):
        await _seed(pool, agency_id, code, [late] * 100, _days(REF_FROM, REF_TO))
        await _seed(pool, agency_id, code, [late] * 100, _days(PERIOD_FROM, PERIOD_TO))

    body = await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO, "&limit=1")
    assert [r[0] for r in body["rows"]] == ["A"]
    assert set(body["reference"]["routes"]) == {"A"}


@pytest.mark.asyncio
async def test_a_route_seen_too_rarely_or_not_at_all_has_its_own_reason(reports_client):  # noqa: F811
    """The agency's window stands as a reference; a route observed fewer than
    100 times in it, or never, does not, and says why."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(REF_FROM, REF_TO))
    await _seed(pool, agency_id, "THIN", [0], _days(REF_FROM, REF_TO))
    for code in ("A", "THIN", "NEW"):
        await _seed(pool, agency_id, code, PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    ref = (await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO))["reference"]
    assert ref["reason"] is None
    assert ref["routes"]["A"]["reason"] is None
    withheld = {"on_time_pct": None, "avg_min": None}
    assert ref["routes"]["THIN"] == {**withheld, "samples": 90, "reason": "insufficient_samples"}
    assert ref["routes"]["NEW"] == {**withheld, "samples": 0, "reason": "no_data"}


@pytest.mark.parametrize(("collected", "reason"), [(71, "insufficient_days"), (72, None)])
@pytest.mark.asyncio
async def test_the_feed_needs_data_on_four_fifths_of_the_days_the_scope_selects(
    reports_client,  # noqa: F811
    collected,
    reason,
):
    """Coverage is the agency's: under 72 of 90 days every route's figure is
    withheld, and the table says so once."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(REF_TO - timedelta(days=collected - 1), REF_TO))
    await _seed(pool, agency_id, "A", PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    ref = (await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO))["reference"]
    assert (ref["days_collected"], ref["min_days"], ref["reason"]) == (collected, 72, reason)
    entry = ref["routes"]["A"]
    assert (entry["reason"], entry["samples"]) == (reason, collected * 100)
    assert (entry["on_time_pct"] is None) == (reason is not None)


@pytest.mark.asyncio
async def test_the_reference_keeps_the_scopes_routes_and_days_of_the_week(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    for day in _days(REF_FROM, REF_TO):
        await _seed(pool, agency_id, "A", [900] * 100 if day.isoweekday() >= 6 else STEADY, [day])
        await _seed(pool, agency_id, "B", [60] * 100, [day])
    for code in ("A", "B"):
        await _seed(pool, agency_id, code, PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    scope = "&dow=weekday&routes=A"
    ref = (await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO, scope))["reference"]
    assert (ref["days_selected"], ref["days_collected"], ref["reason"]) == (64, 64, None)
    assert set(ref["routes"]) == {"A"}
    row = (await _window_rows(client, agency_id, scope))["A"]
    assert ref["routes"]["A"]["on_time_pct"] == float(row[2]) == 60.0


@pytest.mark.asyncio
async def test_a_service_filter_is_resolved_over_the_reference_window(reports_client):  # noqa: F811
    """平日 reads the window's own day types: its weekends and its weekday
    public holidays (2/23, 3/20, 4/29, 5/4-5/6) stay out."""
    client, agency_id, pool = reports_client
    holidays = {date(2026, 2, 23), date(2026, 3, 20), date(2026, 4, 29), date(2026, 5, 4), date(2026, 5, 5)}
    holidays.add(date(2026, 5, 6))
    for day in _days(REF_FROM, REF_TO):
        off = day.isoweekday() >= 6 or day in holidays
        await _seed(pool, agency_id, "A", [900] * 100 if off else STEADY, [day])
    await _seed(pool, agency_id, "A", PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    query = "&service=%E5%B9%B3%E6%97%A5"
    ref = (await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_TO, query))["reference"]
    assert ref["days_selected"] == ref["days_collected"] == 64 - 6
    row = (await _window_rows(client, agency_id, query))["A"]
    assert ref["routes"]["A"]["on_time_pct"] == float(row[2]) == 60.0


@pytest.mark.parametrize("query", ["&time_band=morning", "&time_band=morning&late=1800"])
@pytest.mark.asyncio
async def test_a_scope_no_aggregate_holds_by_date_is_not_compared(
    reports_client,  # noqa: F811
    ch_client,
    ch_async_client,
    query,
):
    """Under a time band every row is a route by hour, which no aggregate
    holds by date, so each listed route is ``not_aggregated``."""
    client, agency_id, pool = reports_client
    await _seed_route_at(pool, agency_id, "A", "平日", PERIOD_FROM.isoformat(), "08:00", [30] * 30)
    await _load_for_path("live", agency_id, ch_client, ch_async_client)

    body = await _report(client, agency_id, "route_table", PERIOD_FROM, PERIOD_FROM, f"&include_sparse=1{query}")
    assert [r[0] for r in body["rows"]] == ["A"]
    ref = body["reference"]
    assert (ref["reason"], ref["samples"], ref["days_selected"]) == ("not_aggregated", 0, 0)
    assert ref["routes"] == {"A": {"on_time_pct": None, "avg_min": None, "samples": 0, "reason": "not_aggregated"}}


@pytest.mark.asyncio
async def test_the_other_reports_carry_no_reference(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(PERIOD_FROM, PERIOD_TO))
    for report in ("ranking", "worst_5min", "compare_ranking"):
        assert (await _report(client, agency_id, report, PERIOD_FROM, PERIOD_TO))["reference"] is None, report


@pytest.mark.asyncio
async def test_the_csv_names_each_routes_reference_and_why_one_is_missing(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(REF_FROM, REF_TO))
    for code in ("A", "NEW"):
        await _seed(pool, agency_id, code, PERIOD, _days(PERIOD_FROM, PERIOD_TO))

    resp = await client.get(
        f"/api/{agency_id}/reports/route_table?from={PERIOD_FROM}&to={PERIOD_TO}&sort=on_time&format=csv"
    )
    assert resp.status_code == 200
    lines = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    header = lines[1]
    assert header[-2:] == ["過去90日(2026-02-17〜2026-05-17)の定時率(%)", "過去90日の定時率を示さない理由"]
    by_code = {line[0]: line for line in lines[2:]}
    assert by_code["A"][-2:] == ["60.0", ""]
    assert by_code["NEW"][-2:] == ["", "観測なし"]


@pytest.mark.parametrize("report", ["route_table", "on_time"])
@pytest.mark.asyncio
async def test_a_failed_reference_read_leaves_the_list_standing(reports_client, monkeypatch, report):  # noqa: F811
    """The reference is context beside the figures, so a failure to read it
    leaves the response without one rather than failing the list."""
    from api.routers import reports as reports_router

    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(PERIOD_FROM, PERIOD_TO))

    async def broken(*_args, **_kwargs):
        raise RuntimeError("reference read failed")

    monkeypatch.setattr(reports_router, "compute_route_references", broken)
    body = await _report(client, agency_id, report, PERIOD_FROM, PERIOD_TO)
    assert body["reference"] is None
    assert [r[0] for r in body["rows"]] == ["A"]


@pytest.mark.asyncio
async def test_the_on_time_csv_reads_no_reference_it_would_not_write(reports_client, monkeypatch):  # noqa: F811
    """Only the Routes table's CSV writes the reference, so the on-time list's
    CSV never reads it; the table's still does."""
    from api.routers import reports as reports_router

    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, "A", STEADY, _days(PERIOD_FROM, PERIOD_TO))
    calls: list[str] = []
    original = reports_router.compute_route_references

    async def counted(*args, **kwargs):
        calls.append("read")
        return await original(*args, **kwargs)

    monkeypatch.setattr(reports_router, "compute_route_references", counted)
    window = f"from={PERIOD_FROM}&to={PERIOD_TO}&format=csv"
    assert (await client.get(f"/api/{agency_id}/reports/on_time?{window}")).status_code == 200
    assert calls == []
    assert (await client.get(f"/api/{agency_id}/reports/route_table?{window}")).status_code == 200
    assert calls == ["read"]
