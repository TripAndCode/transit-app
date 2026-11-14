"""A card's method stamp never costs it its answer: a stamp that cannot be built
leaves the figures standing without one, except a missing aggregate table,
which the aggregate-not-ready handler must still see. The stamp read and the
figures are faked, so no database is involved."""

from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query import tools

_CTX = RangeCtx(from_date=date(2026, 9, 24), to_date=date(2026, 10, 23))


class _NoCalendarConn:
    async def fetch(self, *_args):
        return []


@pytest.fixture(autouse=True)
def figures(monkeypatch):
    async def ranked(*_args, limit=None, **_kwargs):
        return [("R1", None, Decimal("1.00"), Decimal("1.00"), Decimal("2.00"), 300)]

    async def nothing_left_out(*_args):
        return {}

    monkeypatch.setattr(tools, "compute_ranking", ranked)
    monkeypatch.setattr(tools, "left_out_unless_named", nothing_left_out)


def _stamp_raising(monkeypatch, exc: Exception) -> None:
    async def raising(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(tools, "report_definition", raising)


async def test_a_stamp_that_cannot_be_built_leaves_the_figures_without_one(monkeypatch, caplog):
    _stamp_raising(monkeypatch, RuntimeError("stamp read failed"))
    result = await tools.dispatch("top_n", {"metric": "avg_delay"}, _CTX, _NoCalendarConn(), 7, locale="en")
    assert (result.kind, [r[0] for r in result.rows], result.definition) == ("table", ["R1"], None)
    assert "stamp read failed" in caplog.text


async def test_a_missing_aggregate_table_still_reaches_its_handler(monkeypatch):
    _stamp_raising(monkeypatch, asyncpg.exceptions.UndefinedTableError("agg_table_state"))
    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await tools.dispatch("top_n", {"metric": "avg_delay"}, _CTX, _NoCalendarConn(), 7, locale="en")
