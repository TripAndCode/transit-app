"""Unit tests for the ClickHouse query-timeout exception handler (DB-free)."""

import json
import types

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from api.main import app
from api.query_timeout import QUERY_TIMEOUT_CODE, clickhouse_error_handler


def _request(locale="ja"):
    return types.SimpleNamespace(state=types.SimpleNamespace(locale=locale))


def _timeout():
    return DatabaseError(
        "Received ClickHouse exception, code: 159, server response: Code: 159. "
        "DB::Exception: Timeout exceeded: elapsed 30027.117 ms, maximum: 30000.000 ms. (TIMEOUT_EXCEEDED)",
        code=159,
        name="TIMEOUT_EXCEEDED",
    )


@pytest.mark.asyncio
async def test_a_timed_out_query_answers_504_with_a_machine_code():
    resp = await clickhouse_error_handler(_request("en"), _timeout())
    assert resp.status_code == 504
    body = json.loads(bytes(resp.body))
    assert body["code"] == QUERY_TIMEOUT_CODE
    assert body["detail"] == "This took too long to count. Try a shorter period or fewer routes."
    assert "TIMEOUT_EXCEEDED" not in body["detail"]


@pytest.mark.asyncio
async def test_the_timeout_detail_is_localized():
    body = json.loads(bytes((await clickhouse_error_handler(_request("ja"), _timeout())).body))
    assert body["detail"] == "この条件では集計に時間がかかりすぎました。期間を短くするか、路線を絞ってお試しください。"


@pytest.mark.asyncio
async def test_any_other_clickhouse_error_stays_a_server_error():
    other = DatabaseError("Received ClickHouse exception, code: 60", code=60, name="UNKNOWN_TABLE")
    with pytest.raises(DatabaseError):
        await clickhouse_error_handler(_request("en"), other)


def test_the_app_routes_clickhouse_errors_through_the_handler():
    assert app.exception_handlers[DatabaseError] is clickhouse_error_handler
