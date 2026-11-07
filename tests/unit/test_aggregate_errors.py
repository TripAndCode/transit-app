"""Unit tests for the missing-aggregate-table exception handler (DB-free)."""

import json
import types

import asyncpg
import pytest

from api.aggregate_errors import AGGREGATE_NOT_READY_CODE, aggregate_not_ready_handler


def _request(locale="ja"):
    # get_locale reads request.state.locale; a SimpleNamespace is enough.
    return types.SimpleNamespace(state=types.SimpleNamespace(locale=locale))


@pytest.mark.asyncio
async def test_returns_503_with_machine_code():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_route_hour_dow" does not exist')
    resp = await aggregate_not_ready_handler(_request("en"), exc)
    assert resp.status_code == 503
    body = json.loads(bytes(resp.body))
    assert body["code"] == AGGREGATE_NOT_READY_CODE
    assert body["detail"]  # non-empty, user-facing
    # the internal relation name must NOT leak to the client
    assert "agg_route_hour_dow" not in body["detail"]


@pytest.mark.asyncio
async def test_localized_detail():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_feed_health" does not exist')
    en = json.loads(bytes((await aggregate_not_ready_handler(_request("en"), exc)).body))["detail"]
    ja = json.loads(bytes((await aggregate_not_ready_handler(_request("ja"), exc)).body))["detail"]
    assert en != ja  # both provided, locale-specific


@pytest.mark.asyncio
async def test_unknown_locale_falls_back_to_ja():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_x" does not exist')
    body = json.loads(bytes((await aggregate_not_ready_handler(_request("fr"), exc)).body))
    ja_body = json.loads(bytes((await aggregate_not_ready_handler(_request("ja"), exc)).body))
    assert body["detail"] == ja_body["detail"]


@pytest.mark.asyncio
async def test_detail_is_the_exact_localized_string():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_x" does not exist')
    en = json.loads(bytes((await aggregate_not_ready_handler(_request("en"), exc)).body))["detail"]
    ja = json.loads(bytes((await aggregate_not_ready_handler(_request("ja"), exc)).body))["detail"]
    assert en == "Data for this view hasn't been prepared in this environment yet. Please try again later."
    assert ja == "この画面のデータはこの環境ではまだ準備されていません。しばらくしてから再度お試しください。"


@pytest.mark.parametrize("error", [asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.UndefinedColumnError])
def test_a_schema_behind_the_code_answers_503_on_every_read(error):
    """A column a later migration adds is as missing as a table it adds: a read
    that reaches either answers the same 503, never an opaque 500."""
    from api.main import app

    assert app.exception_handlers[error] is aggregate_not_ready_handler


@pytest.mark.asyncio
async def test_a_missing_column_never_names_itself_to_the_client():
    exc = asyncpg.exceptions.UndefinedColumnError("column h.archive_rows does not exist")
    resp = await aggregate_not_ready_handler(_request("en"), exc)
    body = json.loads(bytes(resp.body))
    assert (resp.status_code, body["code"]) == (503, AGGREGATE_NOT_READY_CODE)
    assert "archive_rows" not in body["detail"]
