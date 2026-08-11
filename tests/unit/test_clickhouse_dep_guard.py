"""Tests for api.deps.get_ch's degrade-to-503 behavior (Fix A).

api.main's lifespan sets ``app.state.ch_client = None`` (rather than letting
a ClickHouse connection failure kill the whole app) when ClickHouse is
unreachable or misconfigured at startup. Many routers declare
``ch=Depends(get_ch)`` even though the underlying compute function only
touches ``ch`` on a live-fallback path (the agg-table fast path never calls
it) — see e.g. tests/api/test_reports.py's ``reports_app`` fixture, which
sets ``app.state.ch_client = None`` on purpose because none of its tests
exercise the live path. So ``get_ch`` must NOT eagerly 503 just because the
client is absent (that would 503 Postgres-only requests too, and would make
map.py's ``today_route_summary`` freshness try/except unreachable dead code
since the dependency would fail before the route handler body ever runs).
Instead it hands back a stand-in that only raises when actually used.
"""

import pytest
from fastapi import HTTPException

from api.deps import get_ch


class _FakeState:
    def __init__(self, ch_client):
        self.ch_client = ch_client


class _FakeApp:
    def __init__(self, ch_client):
        self.state = _FakeState(ch_client)


class _FakeRequest:
    def __init__(self, ch_client):
        self.app = _FakeApp(ch_client)


async def test_get_ch_returns_a_stand_in_when_client_is_none_without_raising():
    """Merely resolving the dependency must not fail — a route that never
    ends up needing ClickHouse (e.g. the agg-table fast path) must still work."""
    result = await get_ch(_FakeRequest(None))
    assert result is not None


async def test_get_ch_stand_in_raises_503_only_when_actually_used():
    result = await get_ch(_FakeRequest(None))
    with pytest.raises(HTTPException) as exc_info:
        await result.query("SELECT 1")
    assert exc_info.value.status_code == 503


async def test_get_ch_returns_client_when_present():
    sentinel = object()
    result = await get_ch(_FakeRequest(sentinel))
    assert result is sentinel
