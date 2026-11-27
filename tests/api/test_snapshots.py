"""Frozen snapshots: an admin stores what a screen rendered, and anyone holding
the id reads those exact figures and their method stamp back, however the
aggregates behind the live screen have moved since."""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from api.middleware.ratelimit import limiter
from pipeline.agg_registry import METHOD_VERSION
from tests.api.test_reports import (  # noqa: F401 -- reports_app/reports_client are fixtures
    _record_builds,
    reports_app,
    reports_client,
)
from tests.fixtures.route_dist import seed_route_dist_day
from tests.fixtures.users import seed_session

DAY = "2026-05-14"
ORIGIN = {"Origin": "http://test"}
_BUILT = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)
_REBUILT = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")

# Each screen's create request and the live endpoint the screen itself reads.
_SCREENS = {
    "route_table": ({"from": DAY, "to": DAY, "include_sparse": "1"}, "route_table?from={d}&to={d}&include_sparse=1"),
    "council_report": ({"from": DAY, "to": DAY}, "council_summary?from={d}&to={d}"),
}
_BEFORE = {"A": [0] * 10 + [400] * 15, "B": [0] * 20 + [2000] * 5}
_AFTER = {"A": [0] * 25, "B": [900] * 25}


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


async def _seed(pool, agency_id, delays_by_route):
    for code, delays in delays_by_route.items():
        await seed_route_dist_day(pool, agency_id, code, date.fromisoformat(DAY), delays)


async def _reanalyze(pool, agency_id, delays_by_route, built_at):
    """Rewrite the aggregates the way a rebuild does: new rows, a new build stamp."""
    from pipeline.cache import clear_all
    from pipeline.reports.network import invalidate_network_summary

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM agg_route_daily_dist WHERE agency_id = $1", agency_id)
        await conn.execute("UPDATE agg_table_state SET built_at = $2 WHERE agency_id = $1", agency_id, built_at)
    await _seed(pool, agency_id, delays_by_route)
    clear_all()
    invalidate_network_summary()


async def _create(client, sid, agency_id, screen="route_table", query=None, **extra):
    body = {"screen": screen, "agency_id": agency_id, "query": _SCREENS[screen][0] if query is None else query}
    return await client.post("/api/admin/snapshots", json={**body, **extra}, cookies={"sid": sid}, headers=ORIGIN)


def _figures(report: dict) -> dict:
    """What a report says, without the moment it was served."""
    return {k: v for k, v in report.items() if k != "rendered_at"}


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", sorted(_SCREENS))
async def test_a_snapshot_keeps_its_figures_and_stamp_after_a_reanalyze(reports_client, aconn, screen):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    await _record_builds(pool, (agency_id, "agg_route_daily_dist", _BUILT))
    sid = (await seed_session(aconn, role="admin")).sid
    live_url = f"/api/{agency_id}/reports/" + _SCREENS[screen][1].format(d=DAY)

    before = (await client.get(live_url, headers={"Accept-Language": "ja"})).json()
    created = await _create(client, sid, agency_id, screen)
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["snapshot_id"]
    assert _ID.match(snapshot_id)
    assert created.json()["path"] == f"/s/{snapshot_id}"

    await _reanalyze(pool, agency_id, _AFTER, _REBUILT)
    after = (await client.get(live_url, headers={"Accept-Language": "ja"})).json()
    assert after["rows"] != before["rows"]
    assert after["definition"]["built_at"] != before["definition"]["built_at"]

    # Read back with no session at all: the link is the only credential.
    stored = await client.get(f"/api/snapshots/{snapshot_id}")
    assert stored.status_code == 200
    body = stored.json()
    assert _figures(body["payloads"]["report"]["ja"]) == _figures(before)
    assert body["payloads"]["report"]["ja"]["definition"]["built_at"] == before["definition"]["built_at"]
    assert body["method_version"] == METHOD_VERSION
    assert body["screen"] == screen
    assert "created_by" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", ["ja", "en"])
async def test_the_id_a_printed_council_report_carries_resolves_to_the_figures_on_the_page(
    reports_client,  # noqa: F811
    aconn,
    locale,
):
    """An admin's print of the council report sends this create (the body the
    print flow's own test pins, frontend/src/tabs/AnalysisTab.test.tsx), reads
    the snapshot back, and prints its id only where the stored response in the
    reader's language is the one on the page, all but the moment each was
    served."""
    print_flow = json.loads((Path(__file__).parents[1] / "fixtures" / "council_print_snapshot.json").read_text())
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    query = urlencode(print_flow["body"]["query"])
    assert query == print_flow["search"]
    on_page = await client.get(f"/api/{agency_id}/reports/council_summary?{query}", headers={"Accept-Language": locale})

    created = await client.post(
        "/api/admin/snapshots",
        json={**print_flow["body"], "agency_id": agency_id},
        cookies={"sid": sid},
        headers=ORIGIN,
    )
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["snapshot_id"]
    assert created.json()["path"] == f"/s/{snapshot_id}"

    stored = await client.get(f"/api/snapshots/{snapshot_id}")
    assert stored.status_code == 200
    assert _figures(stored.json()["payloads"]["report"][locale]) == _figures(on_page.json())
    assert created.json()["method_version"] == on_page.json()["definition"]["method_version"] == METHOD_VERSION


@pytest.mark.asyncio
async def test_a_snapshot_holds_both_languages_the_route_catalogue_and_the_resolved_scope(
    reports_client,  # noqa: F811
    aconn,
):
    """The council prose is the server's, in the reader's language, so both are
    stored; the route labels come from the catalogue as it stood; and the
    scope names its dates, never a rolling default."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) "
            "VALUES ($1, 'A', 'A', 'Aline')",
            agency_id,
        )
    sid = (await seed_session(aconn, role="admin")).sid
    snapshot_id = (await _create(client, sid, agency_id, "council_report", title="May council")).json()["snapshot_id"]

    body = (await client.get(f"/api/snapshots/{snapshot_id}")).json()
    reports = body["payloads"]["report"]
    assert set(reports) == {"ja", "en"}
    assert reports["ja"]["rows"] == reports["en"]["rows"]
    assert reports["ja"]["text"] != reports["en"]["text"]
    assert [r["route_id"] for r in body["payloads"]["routes"]] == ["A"]
    assert set(body["payloads"]["credit"]) == {"ja", "en"}
    assert (body["params"]["from"], body["params"]["to"]) == (DAY, DAY)
    assert body["agency_name"] == "Reports Test Agency"
    assert body["title"] == "May council"


@pytest.mark.asyncio
async def test_only_an_admin_creates_lists_or_deletes(reports_client, aconn):  # noqa: F811
    client, agency_id, _ = reports_client
    user = (await seed_session(aconn, role="user")).sid
    body = {"screen": "route_table", "agency_id": agency_id, "query": {}}

    assert (await client.post("/api/admin/snapshots", json=body, headers=ORIGIN)).status_code == 401
    as_user = await client.post("/api/admin/snapshots", json=body, cookies={"sid": user}, headers=ORIGIN)
    assert as_user.status_code == 403
    assert (await client.get("/api/admin/snapshots", cookies={"sid": user})).status_code == 403
    deleted = await client.delete("/api/admin/snapshots/" + "a" * 22, cookies={"sid": user}, headers=ORIGIN)
    assert deleted.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0


@pytest.mark.asyncio
async def test_a_cross_site_create_is_refused(reports_client, aconn):  # noqa: F811
    client, agency_id, _ = reports_client
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await client.post(
        "/api/admin/snapshots",
        json={"screen": "route_table", "agency_id": agency_id, "query": {}},
        cookies={"sid": sid},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"payloads": {"report": {"ja": {"rows": [[99.9]]}}}},
        {"query": {"from": DAY, "rows": "[[99.9]]"}},
        {"screen": "dashboard"},
    ],
    ids=["client-figures", "unknown-param", "unknown-screen"],
)
async def test_the_figures_are_never_the_clients(reports_client, aconn, extra):  # noqa: F811
    """Only the server's own compute fills a snapshot: a body naming figures or
    an unknown parameter is refused, as is a screen it has no computation for."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    body = {"screen": "route_table", "agency_id": agency_id, "query": {"from": DAY, "to": DAY}, **extra}
    resp = await client.post("/api/admin/snapshots", json=body, cookies={"sid": sid}, headers=ORIGIN)
    assert resp.status_code == 422
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0


@pytest.mark.asyncio
async def test_an_unknown_agency_is_404(reports_client, aconn):  # noqa: F811
    client, agency_id, _ = reports_client
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await _create(client, sid, agency_id + 1000)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_an_over_large_payload_is_refused_and_nothing_is_stored(reports_client, aconn, monkeypatch):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    monkeypatch.setattr("api.routers.snapshots.MAX_PAYLOAD_BYTES", 256)
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await _create(client, sid, agency_id)
    assert resp.status_code == 413
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0
    assert await aconn.fetchval("SELECT count(*) FROM admin_audit WHERE action = 'snapshot.create'") == 0


@pytest.mark.asyncio
async def test_locales_that_disagree_on_any_figure_store_nothing(reports_client, aconn, monkeypatch):  # noqa: F811
    """The two languages are two computations; if anything but their prose and
    serving time differs between them (here the row count a rebuild moved),
    the snapshot would freeze two answers, so none is stored."""
    import api.routers.snapshots as snapshots

    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    real = snapshots.render_report

    async def moved_between_locales(*args, **kwargs):
        rendered = await real(*args, **kwargs)
        assert isinstance(rendered, snapshots.ReportResponse)
        if kwargs["locale"] == "en":
            return rendered.model_copy(update={"rows_total": (rendered.rows_total or 0) + 1})
        return rendered

    monkeypatch.setattr(snapshots, "render_report", moved_between_locales)
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await _create(client, sid, agency_id)
    assert resp.status_code == 409
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0
    assert await aconn.fetchval("SELECT count(*) FROM admin_audit WHERE action = 'snapshot.create'") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_id", ["a" * 22, "short", "a" * 21 + "!"])
async def test_an_unknown_or_malformed_id_is_the_same_404(reports_client, snapshot_id):  # noqa: F811
    client, _, _ = reports_client
    resp = await client.get(f"/api/snapshots/{snapshot_id}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "snapshot not found"}


@pytest.mark.asyncio
async def test_an_admin_lists_and_deletes_and_each_change_is_audited(reports_client, aconn):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    sid, uid, _ = await seed_session(aconn, role="admin")
    first = (await _create(client, sid, agency_id, note="draft")).json()["snapshot_id"]
    second = (await _create(client, sid, agency_id, "council_report")).json()["snapshot_id"]
    assert first != second

    listed = (await client.get("/api/admin/snapshots", cookies={"sid": sid})).json()
    assert [s["snapshot_id"] for s in listed] == [second, first]
    assert all(s["created_by"] == uid and "payloads" not in s for s in listed)
    assert listed[1]["note"] == "draft"
    assert listed[1]["agency_id"] == agency_id

    gone = await client.delete(f"/api/admin/snapshots/{first}", cookies={"sid": sid}, headers=ORIGIN)
    assert gone.status_code == 204
    assert (await client.get(f"/api/snapshots/{first}")).status_code == 404
    assert (await client.get(f"/api/snapshots/{second}")).status_code == 200
    again = await client.delete(f"/api/admin/snapshots/{first}", cookies={"sid": sid}, headers=ORIGIN)
    assert again.status_code == 404

    audit = await aconn.fetch(
        "SELECT action, target_id, actor_id, before, after FROM admin_audit WHERE target_type = 'snapshot' ORDER BY id"
    )
    assert [(a["action"], a["target_id"], a["actor_id"]) for a in audit] == [
        ("snapshot.create", first, uid),
        ("snapshot.create", second, uid),
        ("snapshot.delete", first, uid),
    ]
    # The audit names what was frozen, never the figures themselves.
    assert all("payloads" not in (a["before"] or "") + (a["after"] or "") for a in audit)


# Each screen's report and a query with options beyond the defaults.
_TODAY_SCREENS: dict[str, tuple[str, dict[str, Any]]] = {
    "route_table": (
        "route_table",
        {"from": DAY, "to": DAY, "include_sparse": "1", "sort": "on_time", "order": "best", "late": 120, "limit": 1},
    ),
    "council_report": ("council_summary", {"from": DAY, "to": DAY, "preset": "legacy_60s"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", sorted(_TODAY_SCREENS))
async def test_today_recomputes_the_stored_query_as_the_live_screen_would(reports_client, aconn, screen):  # noqa: F811
    """Today's figures come from the screen endpoint's own compute, asked the
    snapshot's stored dates, scope and options, in the reader's language, so
    they differ from the snapshot only where the aggregates moved."""
    client, agency_id, pool = reports_client
    report_type, query = _TODAY_SCREENS[screen]
    await _seed(pool, agency_id, _BEFORE)
    await _record_builds(pool, (agency_id, "agg_route_daily_dist", _BUILT))
    sid = (await seed_session(aconn, role="admin")).sid
    snapshot_id = (await _create(client, sid, agency_id, screen, query=query)).json()["snapshot_id"]
    stored = (await client.get(f"/api/snapshots/{snapshot_id}")).json()["payloads"]["report"]["en"]

    await _reanalyze(pool, agency_id, _AFTER, _REBUILT)
    today = await client.get(f"/api/snapshots/{snapshot_id}/today", headers={"Accept-Language": "en"})
    assert today.status_code == 200, today.text
    body = today.json()
    assert body["unavailable"] is None
    live = f"/api/{agency_id}/reports/{report_type}?{urlencode(query)}"
    expected = await client.get(live, headers={"Accept-Language": "en"})
    assert expected.status_code == 200, expected.text
    assert _figures(body["report"]) == _figures(expected.json())
    assert body["report"]["rows"] != stored["rows"]
    assert body["report"]["definition"]["built_at"] != stored["definition"]["built_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_id", ["a" * 22, "short", "a" * 21 + "!"])
async def test_today_answers_an_unknown_or_malformed_id_as_the_read_does(reports_client, snapshot_id):  # noqa: F811
    client, _, _ = reports_client
    resp = await client.get(f"/api/snapshots/{snapshot_id}/today")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "snapshot not found"}


async def _store(aconn, agency_id, *, screen="route_table", params=None):
    """A snapshot row written directly, for states the create path cannot reach."""
    snapshot_id = "z" * 22
    await aconn.execute(
        "INSERT INTO snapshots (snapshot_id, screen, agency_id, agency_name, params, payloads, method_version) "
        "VALUES ($1, $2, $3, 'Gone Agency', $4::jsonb, '{}'::jsonb, $5)",
        snapshot_id,
        screen,
        agency_id,
        json.dumps({"report_type": "route_table", "from": DAY, "to": DAY} if params is None else params),
        METHOD_VERSION,
    )
    return snapshot_id


@pytest.mark.asyncio
@pytest.mark.parametrize("gone", ["removed", "never-listed"])
async def test_today_says_calmly_when_the_agency_is_gone(reports_client, aconn, gone):  # noqa: F811
    client, agency_id, _ = reports_client
    if gone == "removed":
        await aconn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = $1", agency_id)
    snapshot_id = await _store(aconn, agency_id if gone == "removed" else agency_id + 1000)
    resp = await client.get(f"/api/snapshots/{snapshot_id}/today")
    assert resp.status_code == 200
    assert resp.json() == {"report": None, "unavailable": "agency_removed"}
    assert (await client.get(f"/api/snapshots/{snapshot_id}")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("screen", "params"),
    [
        ("route_table", {"report_type": "route_table", "from": DAY, "to": DAY, "preset": "retired_preset"}),
        ("route_table", {"report_type": "route_table", "from": DAY, "to": DAY, "sort": "retired_sort"}),
        ("route_table", {"report_type": "route_table", "from": DAY, "to": DAY, "dow": "someday"}),
        ("route_page", {"report_type": "route_page", "from": DAY, "to": DAY}),
    ],
    ids=["preset", "sort", "scope", "screen"],
)
async def test_today_says_calmly_when_todays_code_refuses_the_stored_query(
    reports_client,  # noqa: F811
    aconn,
    screen,
    params,
):
    """A stored query is replayed long after it was accepted; one today's code
    no longer takes leaves today's figures unavailable, never an error."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    snapshot_id = await _store(aconn, agency_id, screen=screen, params=params)
    resp = await client.get(f"/api/snapshots/{snapshot_id}/today")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"report": None, "unavailable": "not_computable"}


@pytest.mark.asyncio
async def test_today_is_held_to_a_stricter_limit_than_the_read(reports_client, aconn):  # noqa: F811
    """Today's figures are computed on a public path, so a caller gets fewer
    of them a minute than reads of the stored snapshot."""
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    snapshot_id = (await _create(client, sid, agency_id)).json()["snapshot_id"]
    statuses = [(await client.get(f"/api/snapshots/{snapshot_id}/today")).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]
    assert (await client.get(f"/api/snapshots/{snapshot_id}")).status_code == 200


@pytest.mark.asyncio
async def test_the_read_states_the_agencys_capability_tier_as_it_stands_now(reports_client, aconn):  # noqa: F811
    """A signed-out reader can't reach the agency catalogue, so the read
    carries the tier, derived when it is read and never stored with the
    snapshot."""
    client, agency_id, _ = reports_client
    snapshot_id = await _store(aconn, agency_id)
    await aconn.execute("UPDATE agencies SET ingest_strategy = 'aomori_regex' WHERE agency_id = $1", agency_id)
    assert (await client.get(f"/api/snapshots/{snapshot_id}")).json()["capability_tier"] == "basic"

    await aconn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
    from tests.conftest import confirm_rt_field_coverage

    await confirm_rt_field_coverage(aconn, agency_id)
    assert (await client.get(f"/api/snapshots/{snapshot_id}")).json()["capability_tier"] == "full"
    stored = await aconn.fetchval("SELECT payloads::text FROM snapshots WHERE snapshot_id = $1", snapshot_id)
    assert "capability_tier" not in stored


@pytest.mark.asyncio
async def test_a_snapshot_of_an_agency_no_longer_on_record_has_no_tier(reports_client, aconn):  # noqa: F811
    client, agency_id, _ = reports_client
    snapshot_id = await _store(aconn, agency_id + 1000)
    assert (await client.get(f"/api/snapshots/{snapshot_id}")).json()["capability_tier"] is None


# The route page: one route's own page, whose panels read several endpoints.
_ROUTE = "A"
_PAGE_QUERIES = {
    "default": {"from": DAY, "to": DAY, "routes": _ROUTE},
    "weekday-late": {"from": DAY, "to": DAY, "routes": _ROUTE, "service": "平日", "late": "120"},
    "time-band": {"from": DAY, "to": DAY, "routes": _ROUTE, "time_band": "morning"},
    "variant": {"from": DAY, "to": DAY, "routes": _ROUTE, "shape": "S1"},
}
# Enough observations that the agency's route lists rank both routes, some
# of them late under a 120 s tolerance but on time under the default's.
_PAGE_BEFORE = {"A": [0] * 120 + [200] * 40 + [400] * 40, "B": [0] * 150 + [2000] * 50}
_PAGE_AFTER = {"A": [0] * 200, "B": [900] * 200}


@pytest.fixture
async def route_page_client(reports_app, ch_async_client):  # noqa: F811
    """``reports_client`` with a real ClickHouse client: the route page's delay
    by stop (``GET /route-shape``) reads ``updates`` from ClickHouse."""
    import httpx
    from httpx import ASGITransport

    app, agency_id, pool = reports_app
    app.state.ch_client = ch_async_client
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, pool
    app.state.ch_client = None
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agg_route_daily, static_shapes, route_performance_standards CASCADE")


async def _seed_route_page(pool, ch_client, agency_id):
    """Route A as its page reads it: its list figures, the agency's lateness
    by route, a target, and two observed morning departures on a variant
    whose middle stop has no name, so each language names that stop its own
    way."""
    from tests.conftest import mirror_updates_to_ch
    from tests.fixtures.clock import jst_midday

    await _seed(pool, agency_id, _PAGE_BEFORE)
    day = date.fromisoformat(DAY)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, "
            "worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
            "VALUES ($1, $2, $3, '', 0, 0, 1, 1, $4, 0)",
            agency_id,
            day,
            _ROUTE,
            jst_midday(day),
        )
        for code, late_sec in (("A", 24_000), ("B", 100_000)):
            await conn.execute(
                "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples, "
                "sum_delay_sec, sum_late_sec) VALUES ($1, $2, $3, '', 2.0, 200, $4, $4)",
                agency_id,
                day,
                code,
                late_sec,
            )
        await conn.execute(
            "INSERT INTO route_performance_standards "
            "(agency_id, route_code, metric_type, threshold_value, bonus_malus_rate) "
            "VALUES ($1, $2, 'on_time_pct', 80, 0.01)",
            agency_id,
            _ROUTE,
        )
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) VALUES ($1, 'T1', $2, 'S1')",
            agency_id,
            _ROUTE,
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'ST1', '駅前', 40.82, 140.74, ST_SetSRID(ST_MakePoint(140.74, 40.82), 4326)), "
            "       ($1, 'ST2', NULL, 40.83, 140.75, ST_SetSRID(ST_MakePoint(140.75, 40.83), 4326)), "
            "       ($1, 'ST3', '終点', 40.84, 140.76, ST_SetSRID(ST_MakePoint(140.76, 40.84), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', 1, 'ST1', '08:00:00', '08:00:00'), "
            "       ($1, 'T1', 2, 'ST2', '08:05:00', '08:05:00'), "
            "       ($1, 'T1', 3, 'ST3', '08:10:00', '08:10:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_shapes (agency_id, shape_id, geom) VALUES ($1, 'S1', "
            "ST_SetSRID(ST_MakeLine(ARRAY[ST_MakePoint(140.74, 40.82), ST_MakePoint(140.76, 40.84)]), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', $2, 1, 60, $3, 'test.pb', 'weekday', '08:00:00'), "
            "       ($1, 'T1', $2, 2, 150, $3, 'test.pb', 'weekday', '08:05:00')",
            agency_id,
            _ROUTE,
            jst_midday(day),
        )
    mirror_updates_to_ch(ch_client, agency_id)


async def _live_route_page(client, agency_id, query, locale):
    """What the live route page's hooks fetch for ``query``: the agency's
    route lists and overview over the scope without its route
    (RouteSummaryStrip), the route's targets (PerformanceStandardPanel), and
    its delay by stop on its variant (RouteAnalysisTab)."""
    headers = {"Accept-Language": locale}
    scope = {k: v for k, v in query.items() if k != "shape"}
    agency_wide = urlencode({k: v for k, v in scope.items() if k != "routes"})
    own = urlencode({**scope, "route": _ROUTE})
    base = f"/api/{agency_id}"
    page = {}
    for key in ("ranking", "on_time", "worst_5min"):
        resp = await client.get(f"{base}/reports/{key}?{agency_wide}&limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        page[key] = resp.json()
    standards = await client.get(f"{base}/performance_standards?{own}", headers=headers)
    variant = f"&shape={query['shape']}" if "shape" in query else ""
    shape = await client.get(f"{base}/route-shape?{own}{variant}", headers=headers)
    overview = await client.get(f"{base}/overview/summary?{agency_wide}", headers=headers)
    for resp in (standards, shape, overview):
        assert resp.status_code == 200, resp.text
    page["standards"] = standards.json()
    page["shape"] = shape.json()
    return page, overview.json()["concentration"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_PAGE_QUERIES))
async def test_a_route_page_snapshot_freezes_what_the_live_page_reads(route_page_client, aconn, ch_client, case):
    """Each panel's stored response is the one the live page's own request
    gets, in each language, all but the moment it was served. The agency's
    lists are the same in every language and are stored once, without their
    prose; the map's line is not kept. After a rebuild the stored figures stay
    as they were."""
    client, agency_id, pool = route_page_client
    query = _PAGE_QUERIES[case]
    await _seed_route_page(pool, ch_client, agency_id)
    await _record_builds(pool, (agency_id, "agg_route_daily_dist", _BUILT))
    sid = (await seed_session(aconn, role="admin")).sid
    live = {locale: await _live_route_page(client, agency_id, query, locale) for locale in ("ja", "en")}

    created = await _create(client, sid, agency_id, "route_page", query=query, title="Route A, May")
    assert created.status_code == 201, created.text
    snapshot_id = created.json()["snapshot_id"]
    assert created.json()["screen"] == "route_page"

    await _reanalyze(pool, agency_id, _PAGE_AFTER, _REBUILT)
    moved, _ = await _live_route_page(client, agency_id, query, "ja")
    # A time band is scanned live from the two observed departures, too few
    # to rank; every other case reads the aggregates a rebuild moves.
    if "time_band" not in query:
        assert live["ja"][0]["ranking"]["rows"]
        assert {r["route_code"] for r in live["ja"][1]["top_routes"]} == {"A", "B"}
        assert moved["ranking"]["rows"] != live["ja"][0]["ranking"]["rows"]

    body = (await client.get(f"/api/snapshots/{snapshot_id}")).json()
    stored = body["payloads"]["route_page"]
    lists = body["payloads"]["route_lists"]
    assert set(stored) == {"ja", "en"}
    assert set(lists) == {"ranking", "on_time", "worst_5min"}
    for locale, (page, concentration) in live.items():
        for key, stored_list in lists.items():
            assert _figures(stored_list) == {k: v for k, v in _figures(page[key]).items() if k != "text"}, (locale, key)
        assert set(stored[locale]) == {"standards", "shape"}
        assert stored[locale]["standards"] == page["standards"]
        assert stored[locale]["shape"] == {**page["shape"], "geometry": None}
        assert body["payloads"]["concentration"] == concentration
    assert page["shape"]["geometry"] is not None
    assert [s["stop_sequence"] for s in stored["ja"]["shape"]["stops"]] == [1, 2]
    assert stored["ja"]["shape"]["stops"][1]["stop_name"] != stored["en"]["shape"]["stops"][1]["stop_name"]
    assert [row["metric_type"] for row in stored["ja"]["standards"]["rows"]] == ["on_time_pct"]
    assert stored["ja"]["standards"]["disclaimer"] != stored["en"]["standards"]["disclaimer"]
    assert body["params"]["routes"] == [_ROUTE]
    assert body["params"]["shape"] == "S1"
    assert (body["params"]["from"], body["params"]["to"]) == (DAY, DAY)
    assert body["method_version"] == METHOD_VERSION
    assert body["title"] == "Route A, May"
    assert "report" not in body["payloads"]
    assert set(body["payloads"]["credit"]) == {"ja", "en"}


@pytest.mark.asyncio
async def test_a_route_named_twice_is_one_route(route_page_client, aconn, ch_client):
    """The scope reads its routes stripped and de-duplicated, so a route
    named twice is the one route its page is."""
    client, agency_id, pool = route_page_client
    await _seed_route_page(pool, ch_client, agency_id)
    sid = (await seed_session(aconn, role="admin")).sid
    created = await _create(client, sid, agency_id, "route_page", query={"from": DAY, "to": DAY, "routes": "A, A"})
    assert created.status_code == 201, created.text
    body = (await client.get(f"/api/snapshots/{created.json()['snapshot_id']}")).json()
    assert body["params"]["routes"] == [_ROUTE]


@pytest.mark.asyncio
async def test_route_page_locales_that_disagree_on_a_target_store_nothing(
    route_page_client, aconn, ch_client, monkeypatch
):
    """The targets' figures are two computations too: anything but the
    disclaimer differing between the languages stores nothing."""
    import api.routers.snapshots as snapshots

    client, agency_id, pool = route_page_client
    await _seed_route_page(pool, ch_client, agency_id)
    real = snapshots.render_performance_standards

    async def moved_between_locales(*args, **kwargs):
        rendered = await real(*args, **kwargs)
        if kwargs["locale"] == "en":
            row = rendered.rows[0].model_copy(update={"actual_value": 1.0})
            return rendered.model_copy(update={"rows": [row]})
        return rendered

    monkeypatch.setattr(snapshots, "render_performance_standards", moved_between_locales)
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await _create(client, sid, agency_id, "route_page", query=_PAGE_QUERIES["default"])
    assert resp.status_code == 409
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("screen", "query"),
    [
        ("route_page", {"from": DAY, "to": DAY}),
        ("route_page", {"from": DAY, "to": DAY, "routes": "A,B"}),
        ("route_page", {"from": DAY, "to": DAY, "routes": " "}),
        ("route_page", {"from": DAY, "to": DAY, "routes": "A", "sort": "on_time"}),
        ("route_page", {"from": DAY, "to": DAY, "routes": "A", "preset": "legacy_60s"}),
        ("route_page", {"from": DAY, "to": DAY, "routes": "A", "include_sparse": "1"}),
        ("route_page", {"from": DAY, "to": DAY, "routes": "A", "limit": "5"}),
        ("route_table", {"from": DAY, "to": DAY, "shape": "S1"}),
    ],
    ids=["no-route", "two-routes", "blank-route", "sort", "preset", "sparse", "limit", "shape-off-the-route-page"],
)
async def test_a_query_the_screen_would_never_send_is_refused(reports_client, aconn, screen, query):  # noqa: F811
    """A route page is one route's, under the scope alone (and its variant);
    an option it has no control for, or a variant on another screen, would be
    stored as though it shaped figures it never touched."""
    client, agency_id, _ = reports_client
    sid = (await seed_session(aconn, role="admin")).sid
    resp = await _create(client, sid, agency_id, screen, query=query)
    assert resp.status_code == 422, resp.text
    assert await aconn.fetchval("SELECT count(*) FROM snapshots") == 0


@pytest.mark.asyncio
async def test_today_is_not_computed_for_a_route_page(route_page_client, aconn, ch_client):
    """The comparison reads one report; a route page is several responses,
    so today's figures are unavailable for it, calmly."""
    client, agency_id, pool = route_page_client
    await _seed_route_page(pool, ch_client, agency_id)
    sid = (await seed_session(aconn, role="admin")).sid
    created = await _create(client, sid, agency_id, "route_page", query=_PAGE_QUERIES["default"])
    assert created.status_code == 201, created.text
    resp = await client.get(f"/api/snapshots/{created.json()['snapshot_id']}/today")
    assert resp.status_code == 200
    assert resp.json() == {"report": None, "unavailable": "not_computable"}


_TABLE_OPTIONS = ("sort", "order", "include_sparse", "include_retired", "include_zero_delay", "limit", "preset")
_PANEL_QUERIES = {
    "default": {"from": DAY, "to": DAY, "sort": "on_time"},
    "scoped": {"from": DAY, "to": DAY, "sort": "on_time", "dow": "weekday", "late": "120", "include_sparse": "1"},
    "service": {"from": DAY, "to": DAY, "sort": "on_time", "service": "平日"},
}


async def _live_table_panels(client, agency_id, query, locale):
    """What the Routes screen's two side panels fetch for ``query``: its scope,
    never the table's own options."""
    scope = urlencode({k: v for k, v in query.items() if k not in _TABLE_OPTIONS})
    headers = {"Accept-Language": locale}
    headway = await client.get(f"/api/{agency_id}/headway_quality?{scope}", headers=headers)
    standards = await client.get(f"/api/{agency_id}/performance_standards?{scope}", headers=headers)
    for resp in (headway, standards):
        assert resp.status_code == 200, resp.text
    return headway.json(), standards.json()


@pytest.fixture
async def with_standard(reports_client):  # noqa: F811
    """``reports_client`` with a target on route A and a high-frequency route
    whose headway panel has a row."""
    from tests.conftest import confirm_rt_field_coverage

    client, agency_id, pool = reports_client
    day = date.fromisoformat(DAY)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO route_performance_standards "
            "(agency_id, route_code, metric_type, threshold_value, bonus_malus_rate) "
            "VALUES ($1, 'A', 'on_time_pct', 80, 0.01)",
            agency_id,
        )
        await conn.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", agency_id)
        await conn.execute(
            "INSERT INTO agg_route_headway "
            "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, is_high_frequency) "
            "VALUES ($1, 'A', 480.0, 10, true)",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO agg_route_headway_daily "
            "(agency_id, route_code, date, actual_headway_median_sec, actual_samples, actual_headway_sum_sec, "
            " actual_headway_sumsq_sec2, long_gap_count, scheduled_headway_median_sec, is_high_frequency, "
            " scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2) "
            "VALUES ($1, 'A', $2, 480.0, 2, 960.0, 813600.0, 1, 480.0, true, 4800.0, 2304000.0)",
            agency_id,
            day,
        )
    await confirm_rt_field_coverage(pool, agency_id)
    yield client, agency_id, pool
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE route_performance_standards, agg_route_headway, agg_route_headway_daily CASCADE")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_PANEL_QUERIES))
async def test_a_route_table_snapshot_freezes_the_panels_beside_the_table(with_standard, aconn, case):
    """The standards are stored per language and the headway panel once, each
    the response the live panel's own request gets for the screen's scope, all
    but the moment it was served. Once the agency's targets are removed the
    live panel is empty, and the stored one is as it was."""
    client, agency_id, pool = with_standard
    query = _PANEL_QUERIES[case]
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    live = {locale: await _live_table_panels(client, agency_id, query, locale) for locale in ("ja", "en")}
    assert live["ja"][0]["rows"]
    assert live["ja"][1]["rows"]

    created = await _create(client, sid, agency_id, "route_table", query=query)
    assert created.status_code == 201, created.text
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM route_performance_standards WHERE agency_id = $1", agency_id)
        await conn.execute("DELETE FROM agg_route_headway_daily WHERE agency_id = $1", agency_id)
    moved_headway, moved_standards = await _live_table_panels(client, agency_id, query, "ja")
    assert moved_headway["rows"] == []
    assert moved_standards["rows"] == []

    payloads = (await client.get(f"/api/snapshots/{created.json()['snapshot_id']}")).json()["payloads"]
    assert payloads["headway"] == live["ja"][0] == live["en"][0]
    assert payloads["headway"]["rows"]
    assert set(payloads["standards"]) == {"ja", "en"}
    for locale, (_, standards) in live.items():
        assert payloads["standards"][locale] == standards
    assert payloads["standards"]["ja"]["disclaimer"] != payloads["standards"]["en"]["disclaimer"]


@pytest.mark.asyncio
async def test_the_headway_panel_is_stored_only_where_the_screen_shows_it(with_standard, aconn):
    """The live screen draws headway quality beside the on-time sort alone."""
    client, agency_id, pool = with_standard
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    created = await _create(client, sid, agency_id, "route_table", query={"from": DAY, "to": DAY, "sort": "avg"})
    assert created.status_code == 201, created.text
    payloads = (await client.get(f"/api/snapshots/{created.json()['snapshot_id']}")).json()["payloads"]
    assert "headway" not in payloads
    assert set(payloads["standards"]) == {"ja", "en"}


@pytest.mark.asyncio
async def test_a_council_report_snapshot_stores_no_panels(reports_client, aconn):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    created = await _create(client, sid, agency_id, "council_report")
    payloads = (await client.get(f"/api/snapshots/{created.json()['snapshot_id']}")).json()["payloads"]
    assert not {"headway", "standards"} & set(payloads)


@pytest.mark.asyncio
async def test_route_table_standards_differ_between_languages_only_in_the_disclaimer(with_standard, aconn):
    client, agency_id, pool = with_standard
    await _seed(pool, agency_id, _BEFORE)
    sid = (await seed_session(aconn, role="admin")).sid
    created = await _create(client, sid, agency_id, "route_table", query={"from": DAY, "to": DAY})
    assert created.status_code == 201, created.text
    standards = (await client.get(f"/api/snapshots/{created.json()['snapshot_id']}")).json()["payloads"]["standards"]
    assert standards["ja"]["rows"]
    assert standards["ja"]["disclaimer"] != standards["en"]["disclaimer"]
    assert {k: v for k, v in standards["ja"].items() if k != "disclaimer"} == {
        k: v for k, v in standards["en"].items() if k != "disclaimer"
    }


@pytest.mark.asyncio
async def test_a_route_page_snapshot_whose_floor_rises_after_its_reads_is_refused_and_nothing_is_stored(
    route_page_client, aconn, ch_client, monkeypatch
):
    """The route's delay by stop reads `updates` live, outside any cached
    compute. A prune that commits a floor past the page's day after that read
    passed its check may drop rows the read was still scanning, so the
    snapshot is refused before it is stored, not after. The floor is raised
    once every panel has been read (the route catalogue comes last), so no
    later panel's own check refuses first."""
    from api.routers import snapshots

    client, agency_id, pool = route_page_client
    await _seed_route_page(pool, ch_client, agency_id)
    sid = (await seed_session(aconn, role="admin")).sid
    catalogue = snapshots.fetch_static_routes

    async def catalogue_then_drop(conn, agency):
        await conn.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes, dropped_at) "
            "VALUES ('202605', $1, '2026-07-01', 10, 100, now())",
            [agency],
        )
        return await catalogue(conn, agency)

    monkeypatch.setattr(snapshots, "fetch_static_routes", catalogue_then_drop)
    created = await _create(client, sid, agency_id, "route_page", query=_PAGE_QUERIES["default"], title="Route A")
    assert created.status_code == 422, created.text
    assert created.json()["floor"] == "2026-07-01"
    assert await aconn.fetchval("SELECT count(*) FROM snapshots WHERE agency_id = $1", agency_id) == 0
