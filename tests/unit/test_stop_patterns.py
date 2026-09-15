from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.range import RangeCtx
from pipeline.query.stop_patterns import (
    MAX_GROUPS,
    MAX_SCHEDULE_ROWS,
    MAX_STOPS,
    PatternWindowTooLarge,
    assemble_patterns,
    query_stop_patterns,
)
from pipeline.query.tools import _tool_route_stop_patterns


def stop(trip, seq, stop_id, route="r"):
    return dict(trip_id=trip, stop_sequence=seq, stop_id=stop_id, stop_name=stop_id, route_id=route)


def test_complete_pattern_preserves_missing_and_weights_observations():
    schedule = [stop(trip, seq, name) for trip in ["a", "b"] for seq, name in [(1, "A"), (2, "B"), (3, "A")]]
    rows = assemble_patterns([("a", 1, 600, 2), ("b", 1, 60, 1), ("a", 3, -60, 1)], schedule)
    assert len(rows) == 3
    assert float(rows[0][5]) == 3.67
    assert rows[0][6] == 3
    assert rows[1][5:] == [None, 0]
    assert float(rows[2][5]) == -1
    assert rows[0][3] == rows[2][3]  # loop visit remains distinct


def test_different_order_route_or_pattern_never_merges():
    schedule = [stop("a", 1, "A"), stop("a", 2, "B"), stop("b", 1, "B"), stop("b", 2, "A"),
                stop("c", 1, "A", "other"), stop("c", 2, "B", "other")]
    rows = assemble_patterns([("a", 1, 60, 1), ("b", 1, 120, 1), ("c", 1, 180, 1)], schedule)
    assert len({r[0] for r in rows}) == 3


def test_unmatched_sequence_is_not_silently_joined():
    assert assemble_patterns([("a", 7, 60, 1)], [stop("a", 1, "A")]) == []


@pytest.mark.asyncio
async def test_query_is_scoped_and_does_not_require_coordinates():
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7), dow="weekday", time_band="morning")
    ch = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(result_rows=[("a", 1, 60, 1)])))
    conn = SimpleNamespace(fetch=AsyncMock(return_value=[stop("a", 1, "A"), stop("a", 2, "B")]))
    rows = await query_stop_patterns(9, ctx, conn, ch, route="C10")
    assert len(rows) == 2
    assert rows[1][5:] == [None, 0]
    params = ch.query.call_args.kwargs["parameters"]
    assert params["agency_id"] == 9
    assert "C10" in str(params)
    assert params["ch_from_date"] == ctx.from_date
    assert params["ch_tb_start"] == "05:00"
    assert "toDayOfWeek" in ch.query.call_args.args[0]
    assert conn.fetch.call_args.args[1] == 9
    assert "lat" not in conn.fetch.call_args.args[0]


@pytest.mark.asyncio
async def test_route_scope_conflict_does_not_query():
    ch = SimpleNamespace(query=AsyncMock())
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7), routes=("other",))
    assert await query_stop_patterns(9, ctx, None, ch, route="C10") == []
    ch.query.assert_not_called()


@pytest.mark.asyncio
async def test_too_many_observation_groups_raises():
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7))
    observations = [(f"t{i}", 1, 60, 1) for i in range(MAX_GROUPS + 1)]
    ch = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(result_rows=observations)))
    conn = SimpleNamespace(fetch=AsyncMock())
    with pytest.raises(PatternWindowTooLarge):
        await query_stop_patterns(9, ctx, conn, ch, route="C10")
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_too_many_scheduled_rows_raises_even_with_few_observations():
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7))
    ch = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(result_rows=[("a", 1, 60, 1)])))
    scheduled = [stop("a", seq, f"S{seq}") for seq in range(MAX_SCHEDULE_ROWS + 1)]
    conn = SimpleNamespace(fetch=AsyncMock(return_value=scheduled))
    with pytest.raises(PatternWindowTooLarge):
        await query_stop_patterns(9, ctx, conn, ch, route="C10")


@pytest.mark.asyncio
async def test_too_many_assembled_rows_raises():
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7))
    trips = [f"t{i}" for i in range((MAX_STOPS // 3) + 1)]
    observations = [(trip, 1, 60, 1) for trip in trips]
    ch = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(result_rows=observations)))
    # Distinct stop_ids per trip so each trip forms its own pattern instead of
    # merging into one -- pattern identity is the ordered (seq, stop_id) signature.
    scheduled = [
        stop(trip, seq, f"{trip}-{seq}") for trip in trips for seq in (1, 2, 3)
    ]
    conn = SimpleNamespace(fetch=AsyncMock(return_value=scheduled))
    with pytest.raises(PatternWindowTooLarge):
        await query_stop_patterns(9, ctx, conn, ch, route="C10")


@pytest.mark.asyncio
async def test_tool_handler_reports_too_large_as_empty_result_not_500():
    ctx = RangeCtx(date(2026, 9, 1), date(2026, 9, 7))
    observations = [(f"t{i}", 1, 60, 1) for i in range(MAX_GROUPS + 1)]
    ch = SimpleNamespace(query=AsyncMock(return_value=SimpleNamespace(result_rows=observations)))
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"exists": 1}), fetch=AsyncMock())
    result = await _tool_route_stop_patterns({"route": "C10"}, ctx, conn, 9, "en", ch=ch)
    assert result.kind == "empty"
    assert result.summary == "Narrow the period. The complete stop list exceeds the limit and was not truncated."
