"""dispatch() must validate tool_name against the known-handler allowlist
BEFORE creating any perf label from it. Previously perf.timed_block wrapped
the whole dispatch body including the "unsupported tool" check, so any
caller-supplied tool_name - however bogus - got a permanent, never-evicted
entry in the process-global perf._stats dict. An authenticated user could
grow that dict unbounded by POSTing a unique made-up tool string on every
call (see api/routers/conversations.py's AppendMessage.tool, which never
validates against an allowlist itself).

DB-free: the unsupported-tool path returns before touching conn/agency_id.
"""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline import perf
from pipeline.query.tools import dispatch


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.fixture(autouse=True)
def _reset_perf_stats():
    perf.reset()
    yield
    perf.reset()


@pytest.mark.asyncio
async def test_unsupported_tool_name_does_not_create_a_perf_entry():
    bogus = "not-a-real-tool-xyz"
    result = await dispatch(bogus, {}, _ctx(), conn=None, agency_id=1, locale="ja")
    assert result.kind == "empty"
    assert f"ask.tool.{bogus}" not in perf.snapshot()["ops"]


@pytest.mark.asyncio
async def test_many_unique_bogus_tool_names_leave_perf_stats_empty():
    """The actual abuse scenario: a unique made-up tool name every call must
    never accumulate entries, regardless of how many distinct names are tried."""
    for i in range(20):
        await dispatch(f"bogus-tool-{i}", {}, _ctx(), conn=None, agency_id=1, locale="ja")
    assert perf.snapshot()["ops"] == {}
