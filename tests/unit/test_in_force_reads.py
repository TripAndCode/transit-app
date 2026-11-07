"""A read filed under the timetable in force binds only the versions in force
over the dates it reads, and only REFIT joins the schedule archive."""

from datetime import date

from pipeline import ch_aggregates, static_archive
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.timetable_in_force import Segment

AGENCY = 7
_FORCE = (
    Segment(date(2026, 1, 1), date(2026, 1, 31), "v1"),
    Segment(date(2026, 2, 1), date(2026, 3, 15), "v2"),
    Segment(date(2026, 3, 16), date(2026, 12, 31), "v3"),
)
JOIN = ScheduleJoin(["v1", "v2", "v3"], ["c1", "c2", "c3"], "c3", _FORCE)


def _force(params: dict) -> dict:
    return {key: value for key, value in params.items() if key.startswith("force_")}


def test_a_month_binds_only_the_versions_in_force_in_it():
    _, february = ch_aggregates.select_sql("agg_route_daily", 202602, join=JOIN, stamp=Stamp.REFIT)
    assert _force(february) == {
        "force_firsts": [date(2026, 2, 1)],
        "force_lasts": [date(2026, 3, 15)],
        "force_stems": ["v2"],
        "force_contents": ["c2"],
    }
    _, march = ch_aggregates.select_sql("stop_route_days", 202603, join=JOIN, stamp=Stamp.REFIT)
    assert march["force_stems"] == ["v2", "v3"] and march["force_contents"] == ["c2", "c3"]


def test_some_days_bind_only_the_versions_in_force_on_them():
    _, params = ch_aggregates.select_sql(
        "agg_route_daily", 202603, [date(2026, 3, 20), date(2026, 3, 21)], join=JOIN, stamp=Stamp.REFIT
    )
    assert params["force_stems"] == ["v3"]


def test_a_join_narrowed_to_no_version_binds_empty_lists():
    assert _force(JOIN.params(between=(date(2025, 1, 1), date(2025, 12, 31)))) == {
        "force_firsts": [],
        "force_lasts": [],
        "force_stems": [],
        "force_contents": [],
    }


class _Recording:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def command(self, sql, parameters=None, settings=None):
        self.calls.append((sql, parameters or {}))

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters or {}))

        class _Result:
            result_rows: list = []

        return _Result()


def test_a_one_month_build_binds_that_months_versions_and_names_rows_without_the_archive_join():
    ch = _Recording()
    ch_aggregates.build(
        ch,
        AGENCY,
        {
            "agg_route_daily": ch_aggregates.Rebuild([202602], None),
            "agg_hour_daily": ch_aggregates.Rebuild([202602], None),
            ch_aggregates.ROUTE_STOP_DAYS: ch_aggregates.Rebuild([202602], None),
        },
        join=JOIN,
        refit={date(2026, 2, 10)},
    )
    bound = [(sql, params) for sql, params in ch.calls if "force_stems" in params]
    reads = [sql for sql, _ in bound if "FROM updates" in sql]
    assert reads, "the build read no rows filed under the version in force"
    assert {tuple(params["force_stems"]) for _, params in bound} == {("v2",)}
    assert {tuple(params["force_contents"]) for _, params in bound} == {("c2",)}
    names = [sql for sql, _ in bound if sql.startswith("SELECT ifNull(u.static_version_id, '') AS stem")]
    assert names and not any(static_archive.SCHEDULE in sql for sql in names)
    assert any(static_archive.SCHEDULE in sql for sql in reads if sql not in names)
