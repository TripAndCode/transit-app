"""Free-text tool arguments reach the handlers NFKC-folded, as the question
text is; every other argument reaches them exactly as sent.

DB-free: describe_data never resolves a route, and a ctx without a service
filter never reads the calendar, so dispatch needs no connection here.
"""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.query import tools
from pipeline.query.results import ToolResult
from pipeline.query.schema_linker import LETTER_PREFIX_RE, N_BAN_RE, ROUTE_CODE_RE


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.fixture
def handler_args(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def spy(args, ctx, conn, agency_id, locale, ch=None):
        calls.append(args)
        return ToolResult(kind="empty", summary="")

    monkeypatch.setitem(tools._HANDLERS, "describe_data", spy)
    return calls


@pytest.mark.asyncio
async def test_filter_substring_reaches_the_handler_nfkc_folded(handler_args):
    await tools.dispatch(
        "describe_data",
        {"kind": "stops", "filter_substring": "警固屋５ ＪＲ ｶﾞｽ ５０％"},
        _ctx(),
        conn=None,
        agency_id=1,
    )
    assert handler_args[-1]["filter_substring"] == "警固屋5 JR ガス 50%"


@pytest.mark.asyncio
async def test_enum_and_non_string_arguments_reach_the_handler_unchanged(handler_args):
    """A full-width enum spelling stays invalid rather than being folded into
    a valid value, and a non-string free-text value is passed through as is."""
    args = {"kind": "ｒｏｕｔｅｓ", "order": "ＡＳＣ", "limit": "５０", "filter_substring": 1021}
    await tools.dispatch("describe_data", dict(args), _ctx(), conn=None, agency_id=1)
    assert handler_args[-1] == args


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (ROUTE_CODE_RE, "١٠٢١"),
        (ROUTE_CODE_RE, "１０２１"),
        (LETTER_PREFIX_RE, "A١"),
        (LETTER_PREFIX_RE, "A１"),
        (N_BAN_RE, "١番"),
        (N_BAN_RE, "１番"),
    ],
)
def test_route_shapes_accept_ascii_digits_only(pattern, text):
    """NFKC leaves digits such as Arabic-Indic ones alone, and the SQL each
    shape feeds compares against ASCII route codes and names."""
    assert pattern.match(text) is None


@pytest.mark.parametrize(
    ("pattern", "text"),
    [(ROUTE_CODE_RE, "1021"), (LETTER_PREFIX_RE, "A1"), (LETTER_PREFIX_RE, "l21"), (N_BAN_RE, "1番")],
)
def test_route_shapes_still_accept_their_ascii_spelling(pattern, text):
    assert pattern.match(text) is not None
