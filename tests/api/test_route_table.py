"""The Routes screen's one table: each route's average delay, on-time rate and
share of departures over five minutes late, sorted by whichever the reader
picks."""

import csv
import io
from datetime import date

import pytest

from api.middleware.ratelimit import limiter
from tests.api.test_reports import (  # noqa: F401 -- reports_app/reports_client are fixtures
    _load_for_path,
    _seed_observed,
    _seed_route_at,
    reports_app,
    reports_client,
)

DAY = "2026-05-14"


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Each test asks for the table many ways; the per-minute limit is not what it tests."""
    limiter.reset()


# Spread so that each sort orders the four routes differently:
#   code   avg   on-time  over 5 (share, count)  samples
#   A     4.00    40.0%      60.0%  15             25
#   B     7.33    20.0%      40.0%  10             25
#   C     6.67    80.0%      20.0%   5             25
#   D     2.92    50.0%      50.0%  25             50
_ROUTES = {
    "A": [0] * 10 + [400] * 15,
    "B": [0] * 5 + [200] * 10 + [900] * 10,
    "C": [0] * 20 + [2000] * 5,
    "D": [0] * 25 + [350] * 25,
}

_WORST_FIRST = {
    "avg": ["B", "C", "A", "D"],
    "on_time": ["B", "A", "D", "C"],
    "late5_share": ["A", "D", "B", "C"],
    "late5_count": ["D", "A", "B", "C"],
}


async def _seed_table(pool, agency_id):
    for code, delays in _ROUTES.items():
        await _seed_route_at(pool, agency_id, code, "平日", DAY, "08:00", delays)


def _url(agency_id, band, query=""):
    return f"/api/{agency_id}/reports/route_table?from={DAY}&to={DAY}&include_sparse=1{band}{query}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agg", "live"])
async def test_each_figure_is_the_one_its_own_report_shows(reports_client, ch_client, ch_async_client, path):  # noqa: F811
    """The average is the delay ranking's, the on-time rate and its margin the
    on-time list's, and the count the over-5-minutes list's, route by route;
    the share is that count over the route's observations."""
    client, agency_id, pool = reports_client
    await _seed_table(pool, agency_id)
    band = await _load_for_path(path, agency_id, ch_client, ch_async_client)
    window = f"from={DAY}&to={DAY}{band}"

    table = (await client.get(_url(agency_id, band))).json()
    ranking = (await client.get(f"/api/{agency_id}/reports/ranking?{window}&include_sparse=1")).json()
    on_time = (await client.get(f"/api/{agency_id}/reports/on_time?{window}")).json()
    worst = (await client.get(f"/api/{agency_id}/reports/worst_5min?{window}")).json()
    by_code = {r[0]: r for r in table["rows"]}
    assert set(by_code) == set(_ROUTES)
    for code, delays in _ROUTES.items():
        # (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples, low_confidence, margin_pp)
        row = by_code[code]
        avg = next(r for r in ranking["rows"] if r[0] == code)
        rate = next(r for r in on_time["rows"] if r[0] == code)
        late = next(r for r in worst["rows"] if r[0] == code)
        assert row[1] == avg[2], code
        assert row[2] == rate[2], code
        assert row[4] == late[2], code
        assert row[5] == avg[5] == rate[4] == late[4] == len(delays), code
        assert row[6:] == rate[5:], code
        assert float(row[3]) == round(100 * sum(d > 300 for d in delays) / len(delays), 1), code


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agg", "live"])
async def test_every_sort_ranks_the_same_routes_worst_first_and_best_on_request(
    reports_client,  # noqa: F811
    ch_client,
    ch_async_client,
    path,
):
    client, agency_id, pool = reports_client
    await _seed_table(pool, agency_id)
    band = await _load_for_path(path, agency_id, ch_client, ch_async_client)

    for sort, worst_first in _WORST_FIRST.items():
        worst = (await client.get(_url(agency_id, band, f"&sort={sort}"))).json()
        assert [r[0] for r in worst["rows"]] == worst_first, sort
        assert worst["rows_total"] == 4, sort
        best = (await client.get(_url(agency_id, band, f"&sort={sort}&order=best&limit=2"))).json()
        assert [r[0] for r in best["rows"]] == worst_first[::-1][:2], sort
        assert best["rows_total"] == 4, sort

    # The average leads when no sort is named.
    default = (await client.get(_url(agency_id, band))).json()
    assert [r[0] for r in default["rows"]] == _WORST_FIRST["avg"]


@pytest.mark.asyncio
async def test_ties_break_on_the_route_code_in_either_order(reports_client):  # noqa: F811
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    for code in ("T2", "T1", "T3"):
        await seed_route_dist_day(pool, agency_id, code, date.fromisoformat(DAY), [120] * 100)
    for order in ("", "&order=best"):
        body = (await client.get(_url(agency_id, "", f"&sort=on_time{order}"))).json()
        assert [r[0] for r in body["rows"]] == ["T1", "T2", "T3"], order


@pytest.mark.asyncio
async def test_a_thinly_observed_route_joins_only_on_request(reports_client):  # noqa: F811
    """One observation floor for every sort: a route seen a few dozen times
    would otherwise top the share it reads off them."""
    from pipeline.reports.rankings import RANKING_MIN_SAMPLES
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    day = date.fromisoformat(DAY)
    await seed_route_dist_day(pool, agency_id, "THIN", day, [900] * 30)
    await seed_route_dist_day(pool, agency_id, "SOLID", day, [60] * RANKING_MIN_SAMPLES)
    window = f"from={DAY}&to={DAY}"

    for sort in _WORST_FIRST:
        body = (await client.get(f"/api/{agency_id}/reports/route_table?{window}&sort={sort}")).json()
        assert [r[0] for r in body["rows"]] == ["SOLID"], sort
        assert body["reliable_min_samples"] == RANKING_MIN_SAMPLES
    sparse = (await client.get(f"/api/{agency_id}/reports/route_table?{window}&include_sparse=1")).json()
    assert [r[0] for r in sparse["rows"]] == ["THIN", "SOLID"]


@pytest.mark.asyncio
async def test_left_out_codes_are_counted_over_every_ranked_route(reports_client):  # noqa: F811
    """The table says how many routes it leaves out whatever its sort or
    length, so the count and the routes listed always add up to every route
    it ranks."""
    client, agency_id, pool = reports_client
    await pool.execute(
        "INSERT INTO static_routes (agency_id, route_id) VALUES ($1, 'C1'), ($1, 'C2'), ($1, 'ZERO')",
        agency_id,
    )
    await _seed_observed(pool, agency_id, "ZERO", "2026-09-24", "2026-10-23", delay=0)
    await _seed_observed(pool, agency_id, "BOTH", "2026-09-24", "2026-09-30", delay=0)
    await _seed_observed(pool, agency_id, "C1", "2026-09-24", "2026-10-23", delay=100)
    await _seed_observed(pool, agency_id, "OLD", "2026-09-24", "2026-09-30", delay=200)
    await _seed_observed(pool, agency_id, "C2", "2026-09-24", "2026-10-23", delay=300)
    window = "from=2026-09-24&to=2026-10-23"

    async def table(query):
        resp = await client.get(f"/api/{agency_id}/reports/route_table?{window}{query}")
        assert resp.status_code == 200, (query, resp.text)
        return resp.json()

    for sort in _WORST_FIRST:
        for order in ("", "&order=best"):
            for extra in ("&limit=1", "", "&limit=1&include_retired=1&include_zero_delay=1"):
                query = f"&sort={sort}{order}{extra}"
                body = await table(query)
                assert body["retired_rows"] == 2, query
                assert body["zero_delay_rows"] == 1, query
        current = await table(f"&sort={sort}")
        assert sorted(r[0] for r in current["rows"]) == ["C1", "C2"], sort
        assert current["rows_total"] == 2, sort

    totals = {"&include_retired=1": 4, "&include_zero_delay=1": 3, "&include_retired=1&include_zero_delay=1": 5}
    for toggles, total in totals.items():
        assert (await table(f"&limit=1{toggles}"))["rows_total"] == total, toggles


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agg", "live"])
async def test_the_scopes_tolerance_moves_the_on_time_rate_and_never_the_five_minute_share(
    reports_client,  # noqa: F811
    ch_client,
    ch_async_client,
    path,
):
    client, agency_id, pool = reports_client
    await _seed_route_at(pool, agency_id, "TOL", "平日", DAY, "08:00", [90] * 20 + [400] * 5)
    band = await _load_for_path(path, agency_id, ch_client, ch_async_client)

    default = (await client.get(_url(agency_id, band))).json()
    wider = (await client.get(_url(agency_id, band, "&late=120"))).json()
    assert float(default["rows"][0][2]) == 0.0
    assert float(wider["rows"][0][2]) == 80.0
    for body in (default, wider):
        assert float(body["rows"][0][3]) == 20.0
        assert body["rows"][0][4] == 5
    assert default["definition"]["late_tolerance_sec"] == 60
    assert wider["definition"]["late_tolerance_sec"] == 120
    assert wider["scope_applied"]["late"] is True


@pytest.mark.asyncio
async def test_the_table_takes_a_sort_no_other_report_does(reports_client):  # noqa: F811
    client, agency_id, _ = reports_client
    assert (await client.get(f"/api/{agency_id}/reports/ranking?sort=avg")).status_code == 400
    assert (await client.get(f"/api/{agency_id}/reports/route_table?sort=sideways")).status_code == 422
    assert (await client.get(f"/api/{agency_id}/reports/route_table?order=best")).status_code == 200


@pytest.mark.asyncio
async def test_the_csv_holds_every_figure_and_marks_a_wide_margin(reports_client):  # noqa: F811
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    await seed_route_dist_day(pool, agency_id, "W1", date.fromisoformat(DAY), [0] * 60 + [400] * 40)
    resp = await client.get(_url(agency_id, "", "&format=csv"))
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == f'attachment; filename="route_table_{DAY}_{DAY}.csv"'
    lines = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    assert lines[1] == [
        "系統コード",
        "平均遅延(分)",
        "定時率(%)",
        "5分超の割合(%)",
        "5分超回数",
        "観測数",
        "誤差大",
        "誤差(±ポイント)",
    ]
    # A 60% rate over 100 observations has a 95% interval wider than ±5 points.
    assert lines[2] == ["W1", "2.67", "60.0", "40.0", "40", "100", "誤差大", "9.5"]


@pytest.mark.asyncio
async def test_the_summary_names_the_sort_in_the_requested_language(reports_client):  # noqa: F811
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    await seed_route_dist_day(pool, agency_id, "S1", date.fromisoformat(DAY), [0] * 75 + [400] * 25)
    ja = (await client.get(_url(agency_id, "", "&sort=late5_share"))).json()
    en = (await client.get(_url(agency_id, "", "&sort=late5_share"), headers={"Accept-Language": "en"})).json()
    assert ja["text"] == (
        "【系統一覧：5分超の割合が高い順】\n1位: 路線S1 平均1.7分、定時率75.0%、5分超25.0%（25件）、観測100件"
    )
    assert en["text"] == (
        "[Routes, highest share over 5 min first]\n"
        "#1 route S1 mean 1.7 min, on-time 75.0%, over 5 min 25.0% (25), 100 samples"
    )
