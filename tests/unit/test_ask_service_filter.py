"""Ask resolves the scope's service filter before any tool reads it, and a
tool that swaps the service resolves the new one."""

from datetime import date

import pytest

from api.range import RangeCtx, resolve_service_dates, selected_service_dates
from pipeline.query import tools


class _NoCalendarConn:
    """A connection whose agency has no calendar loaded: the weekday rule decides."""

    async def fetch(self, *_args):
        return []


FROM, TO = date(2026, 9, 14), date(2026, 9, 20)  # Monday to Sunday
WEEKDAYS = [date(2026, 9, d) for d in range(14, 19)]
WEEKEND = [date(2026, 9, 19), date(2026, 9, 20)]


@pytest.fixture
def ranking_ctx(monkeypatch):
    """Record the ctx compute_ranking is asked for."""
    seen: list[RangeCtx] = []

    async def fake_ranking(agency_id, ctx, conn, **_kwargs):
        seen.append(ctx)
        return []

    monkeypatch.setattr(tools, "compute_ranking", fake_ranking)
    return seen


@pytest.mark.asyncio
async def test_dispatch_resolves_the_scope_service_filter(ranking_ctx):
    ctx = RangeCtx(from_date=FROM, to_date=TO, service="平日")
    await tools.dispatch("top_n", {"metric": "avg_delay"}, ctx, _NoCalendarConn(), 7)
    assert selected_service_dates(ranking_ctx[0]) == WEEKDAYS


@pytest.mark.asyncio
async def test_a_date_override_is_resolved_for_its_own_window(ranking_ctx):
    ctx = await resolve_service_dates(RangeCtx(from_date=FROM, to_date=TO, service="平日"), _NoCalendarConn(), 7)
    await tools.dispatch(
        "top_n", {"metric": "avg_delay", "from": "2026-09-07", "to": "2026-09-13"}, ctx, _NoCalendarConn(), 7
    )
    assert selected_service_dates(ranking_ctx[0]) == [date(2026, 9, d) for d in range(7, 12)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chip", "service", "dates"),
    [
        ("weekday", "平日", WEEKDAYS),
        ("weekend", "土日祝", WEEKEND),
        ("saturday", "土曜", WEEKEND[:1]),
        ("sunday_holiday", "日祝", WEEKEND[1:]),
    ],
)
async def test_a_service_chip_replaces_the_scope_filter_with_its_own_dates(ranking_ctx, chip, service, dates):
    ctx = RangeCtx(from_date=FROM, to_date=TO, service="平日")
    await tools.dispatch("top_n", {"metric": "avg_delay", "service_type": chip}, ctx, _NoCalendarConn(), 7)
    assert ranking_ctx[0].service == service
    assert selected_service_dates(ranking_ctx[0]) == dates


@pytest.mark.asyncio
async def test_the_all_chip_drops_the_scope_filter(ranking_ctx):
    ctx = RangeCtx(from_date=FROM, to_date=TO, service="平日")
    await tools.dispatch("top_n", {"metric": "avg_delay", "service_type": "all"}, ctx, _NoCalendarConn(), 7)
    assert ranking_ctx[0].service == "all"
    assert selected_service_dates(ranking_ctx[0]) is None
