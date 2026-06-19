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
    body = json.loads(resp.body)
    assert body["code"] == AGGREGATE_NOT_READY_CODE
    assert body["detail"]  # non-empty, user-facing
    # the internal relation name must NOT leak to the client
    assert "agg_route_hour_dow" not in body["detail"]


@pytest.mark.asyncio
async def test_localized_detail():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_feed_health" does not exist')
    en = json.loads((await aggregate_not_ready_handler(_request("en"), exc)).body)["detail"]
    ja = json.loads((await aggregate_not_ready_handler(_request("ja"), exc)).body)["detail"]
    assert en != ja  # both provided, locale-specific


@pytest.mark.asyncio
async def test_unknown_locale_falls_back_to_ja():
    exc = asyncpg.exceptions.UndefinedTableError('relation "agg_x" does not exist')
    body = json.loads((await aggregate_not_ready_handler(_request("fr"), exc)).body)
    ja_body = json.loads((await aggregate_not_ready_handler(_request("ja"), exc)).body)
    assert body["detail"] == ja_body["detail"]
