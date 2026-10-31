"""schedule_realism_padding filters its route on raw `updates` rows, ahead of the
per-visit aggregation, never on the aggregated visits afterwards: a stop
visit's route_code is an aggregate, so a filter there would aggregate every
route in the window first."""

from datetime import date

from api.range import RangeCtx


class _EmptyResult:
    column_names = ("trip_id", "date", "stop_sequence", "scheduled_sec", "dep_delay", "arr_delay")
    result_rows: list = []


class _RecordingCh:
    def __init__(self):
        self.calls = []

    async def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return _EmptyResult()


async def test_schedule_realism_padding_filters_the_route_before_aggregating(monkeypatch):
    from pipeline.query import tool_queries

    async def confirmed(*_args):
        return True

    monkeypatch.setattr(tool_queries, "rt_field_coverage_confirmed", confirmed)
    ch = _RecordingCh()
    ctx = RangeCtx(from_date=date(2026, 4, 1), to_date=date(2026, 4, 7))

    result = await tool_queries.schedule_realism_padding(1, ctx, None, ch, route="R9")

    assert result["rows"] == []
    ((sql, parameters),) = ch.calls
    route_filter = "route_code IN {ch_routes:Array(String)}"
    assert parameters["ch_routes"] == ["R9"]
    assert sql.count(route_filter) == 1
    assert sql.index(route_filter) < sql.index(" GROUP BY ")
    after_cte = sql[sql.rindex(")") :]
    assert "route_code" not in after_cte
