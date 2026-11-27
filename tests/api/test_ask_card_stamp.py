"""A saved Ask card carries the method stamp the report of the same query
carries, signed in (stored on the message) and signed out (returned with the
card for the browser's own thread)."""

from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport

from pipeline.reports.definition import DefinitionMeta, format_definition_csv_line
from tests.api.test_reports import (
    _record_contents,
    _record_timetables,
    _run_analyze,
    _seed_route,
    reports_app,  # noqa: F401  (fixture)
)
from tests.conftest import TEST_ORIGIN

_CSRF = {"Origin": TEST_ORIGIN}
_FROM, _TO = "2026-05-18", "2026-05-24"
_FILTERS = {"from_date": _FROM, "to_date": _TO}


@pytest.fixture
async def stamped_agency(reports_app, ch_client, ch_async_client):  # noqa: F811
    """An agency with one route observed on a weekday and a weekend day of
    the range and analyzed, so a card has rows and its stamp names a build,
    plus a timetable named and hashed on two other days and a signed-in user."""
    app, agency_id, pool = reports_app
    app.state.ch_client = ch_async_client
    await pool.execute(
        "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, 'R1', 'R1')", agency_id
    )
    for day, service in (("2026-05-18", "平日"), ("2026-05-23", "土曜")):
        await _seed_route(pool, agency_id, "R1", service, day, [30] * 20 + [400] * 5)
    _run_analyze(agency_id, ch_client)
    await _record_timetables(
        pool, agency_id, {"2026-05-20": "gtfs_static_20260401", "2026-05-21": "gtfs_static_20260401"}
    )
    await _record_contents(pool, agency_id, {"gtfs_static_20260401": "0123456789abcdef"})
    user_id = await pool.fetchval(
        "INSERT INTO users (email, name, role) VALUES ('stamp@test', 'S', 'user') RETURNING user_id"
    )
    yield app, agency_id, user_id, pool
    await pool.execute("DELETE FROM ask_conversations")
    await pool.execute("DELETE FROM users WHERE email = 'stamp@test'")


@asynccontextmanager
async def _client(app, user_id: int | None = None):
    from api.deps import get_current_user
    from api.security import User

    if user_id is not None:
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=user_id,
            email="stamp@test",
            name="S",
            avatar_url=None,
            role="user",
            suspended_at=None,
            llm_approved=False,
        )
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def _saved_card(client, agency_id: int, tool: str, args: dict) -> dict:
    conv = await client.post(
        f"/api/{agency_id}/conversations", json={"title": "T", "filter_ctx": _FILTERS}, headers=_CSRF
    )
    conv_id = conv.json()["conversation_id"]
    resp = await client.post(
        f"/api/{agency_id}/conversations/{conv_id}/messages", json={"tool": tool, "args": args}, headers=_CSRF
    )
    assert resp.status_code == 200, resp.text
    [_user, saved] = (await client.get(f"/api/{agency_id}/conversations/{conv_id}/messages")).json()
    assert saved["definition"] == resp.json()["assistant"]["definition"]
    return saved


# Each card the notebook offers whose figures a report computes, with that
# report's query over the same range and filters.
_REPORTED_CARDS = [
    pytest.param("on_time", {"k": 5, "best_first": "false"}, "on_time", "", id="ontime_rank"),
    pytest.param("top_n", {"metric": "avg_delay", "k": 5, "service_type": "all"}, "ranking", "", id="top_delay"),
    pytest.param("top_n", {"metric": "on_time_rate", "k": 5}, "on_time", "", id="top_on_time_rate"),
    pytest.param("top_n", {"metric": "worst_5min", "k": 5}, "worst_5min", "", id="top_worst_5min"),
    pytest.param(
        "cmp_service",
        {"metric": "avg_delay", "route_code": "R1"},
        "compare_ranking",
        "&routes=R1",
        id="weekday_weekend",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "report_type", "query"), _REPORTED_CARDS)
async def test_a_saved_card_carries_the_stamp_its_report_carries(stamped_agency, tool, args, report_type, query):
    app, agency_id, user_id, _pool = stamped_agency
    async with _client(app, user_id) as client:
        saved = await _saved_card(client, agency_id, tool, args)
        report = await client.get(f"/api/{agency_id}/reports/{report_type}?from={_FROM}&to={_TO}{query}")
    assert report.status_code == 200, report.text
    expected = report.json()["definition"]
    assert expected["built_at"] is not None
    assert ("gtfs_static_20260401", "01234567") in {
        (v["name"], v["content_hash"]) for v in expected["timetable_versions"]
    }
    assert saved["result"]["rows"]
    assert saved["definition"] == expected


@pytest.mark.asyncio
async def test_a_saved_trend_card_carries_the_stamp_of_the_trend_export(stamped_agency):
    """The card draws the daily series alone, which is what the trend export
    writes, so its stamp is the export's rather than the screen's, whose
    hourly heatmap a route filter sends to a live scan."""
    app, agency_id, user_id, _pool = stamped_agency
    args = {"metric": "avg_delay", "route_code": "R1", "granularity": "week"}
    async with _client(app, user_id) as client:
        saved = await _saved_card(client, agency_id, "trend", args)
        export = await client.get(f"/api/{agency_id}/reports/trend?from={_FROM}&to={_TO}&routes=R1&format=csv")
    assert export.status_code == 200, export.text
    preamble = next(csv.reader(io.StringIO(export.text.lstrip("﻿"))))[0]
    stamp = DefinitionMeta.model_validate(saved["definition"])
    assert saved["result"]["series"]
    assert stamp.source == "aggregates" and stamp.built_at is not None
    assert format_definition_csv_line(stamp) == preamble


@pytest.mark.asyncio
async def test_a_card_no_report_computes_is_saved_without_a_stamp(stamped_agency):
    app, agency_id, user_id, _pool = stamped_agency
    async with _client(app, user_id) as client:
        saved = await _saved_card(client, agency_id, "describe_data", {})
    assert saved["definition"] is None


@pytest.mark.asyncio
async def test_a_signed_out_card_returns_the_stamp_a_signed_in_one_saves(stamped_agency):
    """Signed out, the card is answered without being stored, and the stamp
    comes back with it for the browser's own thread."""
    app, agency_id, user_id, pool = stamped_agency
    args = {"k": 5, "best_first": "false"}
    async with _client(app) as client:
        resp = await client.post(
            f"/api/{agency_id}/ask/card", json={"tool": "on_time", "args": args, "filter_ctx": _FILTERS}, headers=_CSRF
        )
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert await pool.fetchval("SELECT count(*) FROM ask_conversation_messages") == 0
    async with _client(app, user_id) as client:
        saved = await _saved_card(client, agency_id, "on_time", args)
    for field in ("tool", "args", "signature_hash", "result", "rendered_summary", "conditions", "definition"):
        assert card[field] == saved[field], field
    assert card["definition"] is not None


@pytest.mark.asyncio
async def test_a_signed_out_card_needs_a_same_site_origin(stamped_agency):
    app, agency_id, _user_id, _pool = stamped_agency
    async with _client(app) as client:
        resp = await client.post(f"/api/{agency_id}/ask/card", json={"tool": "on_time", "args": {}})
    assert resp.status_code == 403
