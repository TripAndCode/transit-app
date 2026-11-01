"""The data-source credit in a report CSV's preamble.

Every report CSV opens with a preamble (the definition line, then any
caveats) ahead of its column header. Its last preamble row credits the open
data the agency's feed is published under, and names no other source. The
credit is in the language the request names in ``lang`` (the app's UI
language), else its Accept-Language; ``lang`` changes no other row. A feed no
source covers adds no row, so the file keeps its shape.
"""

import csv
import io
import json
from pathlib import Path

import pytest

from api.routers.reports import _REPORT_CSV_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
# Shared with tests/unit/test_data_sources.py and the frontend's print tests.
CREDIT_LINES: dict[str, dict[str, str]] = json.loads(
    (ROOT / "tests" / "fixtures" / "data_source_credit_lines.json").read_text(encoding="utf-8")
)
AOMORI = "https://aomoricitybus.com/TripUpdate.pb"
HIROSHIMA = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin"
DAY = "2026-06-15"
REPORT_TYPES = ["council_summary", "delay_certificate", "ranking"]
ALL_CREDITS = {line for lines in CREDIT_LINES.values() for line in lines.values()}


async def _csv_rows(
    client, agency_id: int, report_type: str, accept_language: str, ui_lang: str | None = None
) -> list[list[str]]:
    lang = "" if ui_lang is None else f"&lang={ui_lang}"
    resp = await client.get(
        f"/api/{agency_id}/reports/{report_type}?from={DAY}&to={DAY}&format=csv{lang}",
        headers={"Accept-Language": accept_language},
    )
    assert resp.status_code == 200, resp.text
    return list(csv.reader(io.StringIO(resp.text)))


def _set_feed(pg_conn, agency_id: int, feed_url: str) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE agencies SET feed_url=%s WHERE agency_id=%s", (feed_url, agency_id))
    pg_conn.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", REPORT_TYPES)
@pytest.mark.parametrize(("feed_url", "lang"), [(AOMORI, "ja"), (AOMORI, "en"), (HIROSHIMA, "ja"), (HIROSHIMA, "en")])
async def test_preamble_ends_with_the_agency_source_credit(
    client, agency_id, pg_conn, ch_async_client, report_type, feed_url, lang
):
    from api.main import app

    app.state.ch_client = ch_async_client
    _set_feed(pg_conn, agency_id, feed_url)

    rows = await _csv_rows(client, agency_id, report_type, lang)
    header_idx = rows.index(_REPORT_CSV_COLUMNS[report_type])
    credit = CREDIT_LINES[feed_url][lang]
    assert rows[header_idx - 1] == [credit]
    assert [cell for row in rows for cell in row if cell in ALL_CREDITS] == [credit]


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", REPORT_TYPES)
async def test_a_feed_no_source_covers_adds_no_credit_row(client, agency_id, ch_async_client, report_type):
    from api.main import app

    app.state.ch_client = ch_async_client

    rows = await _csv_rows(client, agency_id, report_type, "ja")
    header_idx = rows.index(_REPORT_CSV_COLUMNS[report_type])
    assert not any("https://" in cell for row in rows[:header_idx] for cell in row)
    if report_type == "ranking":
        assert header_idx == 1  # the definition line alone, as before


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", REPORT_TYPES)
@pytest.mark.parametrize(("ui_lang", "accept_language"), [("ja", "en"), ("en", "ja")])
async def test_the_ui_language_wins_over_accept_language_for_the_credit(
    client, agency_id, pg_conn, ch_async_client, report_type, ui_lang, accept_language
):
    from api.main import app

    app.state.ch_client = ch_async_client
    _set_feed(pg_conn, agency_id, HIROSHIMA)

    rows = await _csv_rows(client, agency_id, report_type, accept_language, ui_lang)
    header_idx = rows.index(_REPORT_CSV_COLUMNS[report_type])
    assert rows[header_idx - 1] == [CREDIT_LINES[HIROSHIMA][ui_lang]]


@pytest.mark.asyncio
@pytest.mark.parametrize("report_type", REPORT_TYPES)
async def test_lang_changes_only_the_credit_row(client, agency_id, pg_conn, ch_async_client, report_type):
    from api.main import app

    app.state.ch_client = ch_async_client
    _set_feed(pg_conn, agency_id, AOMORI)

    in_ja = await _csv_rows(client, agency_id, report_type, "en", "ja")
    in_en = await _csv_rows(client, agency_id, report_type, "en", "en")
    assert [CREDIT_LINES[AOMORI]["ja"]] in in_ja
    assert [row for row in in_ja if row != [CREDIT_LINES[AOMORI]["ja"]]] == [
        row for row in in_en if row != [CREDIT_LINES[AOMORI]["en"]]
    ]


@pytest.mark.asyncio
async def test_lang_names_only_the_two_ui_languages(client, agency_id):
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={DAY}&to={DAY}&format=csv&lang=fr")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_lang_applies_only_to_a_csv(client, agency_id):
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={DAY}&to={DAY}&lang=en")
    assert resp.status_code == 400
