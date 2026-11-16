"""An aggregate-backed export whose period reaches dates before the agency's
retention floor names the method those dates were frozen under, as the drop
recorded it: in its JSON stamp always, and in its rendered text when that
method is not the current one or was not recorded."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from api.range import jst_today
from pipeline.agg_registry import METHOD_VERSION
from pipeline.reports import definition
from pipeline.reports.definition import PreFloorMethod
from pipeline.retention import floor_after, partition_span

_OLD_METHOD = "2026.11.0"


def _month_start_back(months: int):
    first = jst_today().replace(day=1)
    for _ in range(months):
        first = (first - timedelta(days=1)).replace(day=1)
    return first


PARTITION = _month_start_back(3).strftime("%Y%m")
FLOOR = floor_after(partition_span(PARTITION)[1])
BEFORE = FLOOR - timedelta(days=3)
LAST_FROZEN = FLOOR - timedelta(days=1)
EN = {"Accept-Language": "en"}


@pytest.fixture
def stamp_app(ch_async_client):
    from api.main import app

    app.state.ch_client = ch_async_client
    return app


def _record_drop(pg_conn, agency_id: int, method: str | None) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months "
            "(partition_id, agency_ids, retention_floor, rows, bytes, dropped_at, method_version) "
            "VALUES (%s, %s, %s, 10, 100, now(), %s)",
            (PARTITION, [agency_id], FLOOR, method),
        )
    pg_conn.commit()


def _preamble(resp) -> str:
    assert resp.status_code == 200, resp.text
    return resp.text.lstrip("﻿").splitlines()[0]


@pytest.mark.asyncio
async def test_a_report_reaching_dates_frozen_under_an_earlier_method_names_it(client, agency_id, pg_conn, stamp_app):
    _record_drop(pg_conn, agency_id, _OLD_METHOD)
    url = f"/api/{agency_id}/reports/ranking?from={BEFORE}&to={FLOOR}"

    resp = await client.get(url, headers=EN)
    assert resp.status_code == 200, resp.text
    definition = resp.json()["definition"]
    assert definition["method_version"] == METHOD_VERSION
    assert definition["pre_floor_methods"] == [
        {
            "agency_id": agency_id,
            "from_date": BEFORE.isoformat(),
            "to_date": LAST_FROZEN.isoformat(),
            "method_version": _OLD_METHOD,
        }
    ]
    assert _preamble(await client.get(f"{url}&format=csv")).endswith(
        f"; 保存期間を過ぎた日の算出方法={BEFORE}〜{LAST_FROZEN}は算出方法 {_OLD_METHOD}"
    )


@pytest.mark.asyncio
async def test_the_council_report_text_names_the_earlier_method(client, agency_id, pg_conn, stamp_app):
    _record_drop(pg_conn, agency_id, _OLD_METHOD)
    resp = await client.get(f"/api/{agency_id}/reports/council_summary?from={BEFORE}&to={FLOOR}", headers=EN)
    assert resp.status_code == 200, resp.text
    assert (
        "Figures for dates whose detailed readings are no longer kept stay as analyzed then: "
        f"{BEFORE} to {LAST_FROZEN} under method {_OLD_METHOD}"
    ) in resp.json()["text"]


@pytest.mark.asyncio
async def test_a_drop_that_recorded_no_method_is_named_as_not_recorded(client, agency_id, pg_conn, stamp_app):
    _record_drop(pg_conn, agency_id, None)
    url = f"/api/{agency_id}/reports/ranking?from={BEFORE}&to={FLOOR}"
    resp = await client.get(url, headers=EN)
    assert resp.json()["definition"]["pre_floor_methods"][0]["method_version"] is None
    assert _preamble(await client.get(f"{url}&format=csv")).endswith(
        f"; 保存期間を過ぎた日の算出方法={BEFORE}〜{LAST_FROZEN}は算出方法の記録なし"
    )


@pytest.mark.asyncio
async def test_dates_frozen_under_the_current_method_are_stated_but_not_noted(client, agency_id, pg_conn, stamp_app):
    _record_drop(pg_conn, agency_id, METHOD_VERSION)
    url = f"/api/{agency_id}/reports/ranking?from={BEFORE}&to={FLOOR}"
    resp = await client.get(url, headers=EN)
    assert resp.json()["definition"]["pre_floor_methods"][0]["method_version"] == METHOD_VERSION
    assert "保存期間を過ぎた日" not in _preamble(await client.get(f"{url}&format=csv"))


@pytest.mark.asyncio
async def test_a_report_from_the_floor_on_names_no_frozen_method(client, agency_id, pg_conn, stamp_app):
    _record_drop(pg_conn, agency_id, _OLD_METHOD)
    url = f"/api/{agency_id}/reports/ranking?from={FLOOR}&to={FLOOR}"
    resp = await client.get(url, headers=EN)
    assert resp.json()["definition"]["pre_floor_methods"] is None
    assert "保存期間を過ぎた日" not in _preamble(await client.get(f"{url}&format=csv"))


def _agency(pg_conn, name: str, *, deleted: bool = False) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, CASE WHEN %s THEN now() END) "
            "RETURNING agency_id",
            (name, f"http://example.com/{name}.pb", deleted),
        )
        agency = cur.fetchone()[0]
    pg_conn.commit()
    return agency


def _drop(pg_conn, agency_ids: list[int], floor: date, method: str | None) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months "
            "(partition_id, agency_ids, retention_floor, rows, bytes, method_version) "
            "VALUES ('202412', %s, %s, 1, 1, %s)",
            (agency_ids, floor, method),
        )
    pg_conn.commit()


@pytest.mark.asyncio
async def test_the_board_names_each_listed_agencys_frozen_spans_and_no_deleted_agencys(aconn, pg_conn, agency_id):
    """Drops recorded across agencies interleave by id, so each agency's spans
    are read apart; a soft-deleted agency the board does not list is left out."""
    other = _agency(pg_conn, "b")
    deleted = _agency(pg_conn, "c", deleted=True)
    _drop(pg_conn, [agency_id], date(2025, 2, 1), _OLD_METHOD)
    _drop(pg_conn, [other], date(2025, 2, 1), None)
    _drop(pg_conn, [agency_id, deleted], date(2025, 4, 1), "2026.11.15")

    spans = await definition._read_pre_floor(aconn, None, date(2025, 1, 1), date(2025, 6, 30))

    assert spans == [
        PreFloorMethod(
            agency_id=agency_id, from_date=date(2025, 1, 1), to_date=date(2025, 1, 31), method_version=_OLD_METHOD
        ),
        PreFloorMethod(
            agency_id=agency_id, from_date=date(2025, 2, 1), to_date=date(2025, 3, 31), method_version="2026.11.15"
        ),
        PreFloorMethod(agency_id=other, from_date=date(2025, 1, 1), to_date=date(2025, 1, 31), method_version=None),
    ]
