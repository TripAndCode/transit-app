from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.range import RangeCtx
from pipeline.query.stop_patterns import assemble_patterns, query_stop_patterns


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
    rows = await query_stop_patterns(9, ctx, conn, ch, "C10")
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
    assert await query_stop_patterns(9, ctx, None, ch, "C10") == []
    ch.query.assert_not_called()
