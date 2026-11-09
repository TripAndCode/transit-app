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
    on_page = await client.get(
        f"/api/{agency_id}/reports/council_summary?{query}", headers={"Accept-Language": locale}
    )

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
        {"screen": "route_page"},
    ],
    ids=["client-figures", "unknown-param", "route-page"],
)
async def test_the_figures_are_never_the_clients(reports_client, aconn, extra):  # noqa: F811
    """Only the server's own compute fills a snapshot: a body naming figures or
    an unknown parameter is refused, as is a screen this slice cannot render."""
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
