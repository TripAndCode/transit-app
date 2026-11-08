"""A live read binds the versions in force over the dates it reads, refuses a
read outside them, and its definition stamp names the version each day was
filed under."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.live_filing import AS_INGESTED, Filing, refits
from pipeline.reports.filters import _dedup_cte_ch
from pipeline.reports.schedule_revision import ScheduleDays
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.timetable_in_force import Segment

JOIN = ScheduleJoin(
    ["v1", "v2"],
    ["c1", "c2"],
    "",
    (Segment(date(2026, 9, 1), date(2026, 9, 30), "v1"), Segment(date(2026, 10, 1), date(2026, 12, 31), "v2")),
)


def test_a_filing_binds_only_the_versions_in_force_over_its_dates():
    params = JOIN.filing_params((date(2026, 10, 3), date(2026, 10, 9)))
    assert params == {
        "join_stems": ["v1", "v2"],
        "join_hashes": ["c1", "c2"],
        "force_firsts": [date(2026, 10, 1)],
        "force_lasts": [date(2026, 12, 31)],
        "force_stems": ["v2"],
        "force_contents": ["c2"],
    }


def test_a_read_outside_its_filings_dates_is_refused():
    filing = Filing(Stamp.REFIT, (date(2026, 10, 3), date(2026, 10, 9)), JOIN.filing_params((date(2026, 10, 3),) * 2))
    with pytest.raises(ValueError, match="reaches outside"):
        _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 2), to_date=date(2026, 10, 9)), filing)
    _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9)), filing)


def test_rows_read_as_ingested_bind_nothing_and_never_join_the_archive():
    sql, params = _dedup_cte_ch(RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9)), AS_INGESTED)
    assert "static_schedule_archive" not in sql
    assert not [name for name in params if name.startswith(("force_", "join_"))]


def test_a_refit_read_joins_the_archive_and_tests_the_band_and_route_on_the_filed_row():
    ctx = RangeCtx(from_date=date(2026, 10, 3), to_date=date(2026, 10, 9), time_band="noon", routes=("R1",))
    between = (ctx.from_date, ctx.to_date)
    sql, params = _dedup_cte_ch(ctx, Filing(Stamp.REFIT, between, JOIN.filing_params(between)))
    # Narrowed first to the trips the band can keep, then joined on the hashed key.
    assert "u.trip_id IN (SELECT trip_id FROM static_schedule_archive" in sql
    filed = sql[sql.index("ANY LEFT JOIN") :]
    assert "archive_key" in filed
    assert "route_code IN {ch_routes:Array(String)}" in filed
    assert "{ch_tb_start:UInt32}" in filed
    assert params["force_stems"] == ["v2"]


def test_the_stamp_of_a_live_scan_names_the_version_each_day_was_filed_under():
    schedule = ScheduleDays(
        date(2026, 9, 30),
        date(2026, 10, 2),
        [
            (8, date(2026, 9, 29), "v1"),
            (8, date(2026, 9, 30), "v1"),
            # Recorded by an analyze that ran before v2's span was known.
            (8, date(2026, 10, 1), "v1"),
            (8, date(2026, 10, 2), "v1"),
            (9, date(2026, 10, 1), "other"),
        ],
    )
    filed = schedule.filed_under(8, JOIN.force)
    assert filed.rows == [
        (8, date(2026, 9, 29), "v1"),
        (8, date(2026, 9, 30), "v1"),
        (8, date(2026, 10, 1), "v2"),
        (8, date(2026, 10, 2), "v2"),
        (9, date(2026, 10, 1), "other"),
    ]
    assert filed.boundaries() == ["2026-10-01"]


def test_a_day_no_version_covers_keeps_the_version_analyze_recorded():
    schedule = ScheduleDays(date(2027, 1, 1), date(2027, 1, 1), [(8, date(2027, 1, 1), "ingested"), (8, None, None)])
    assert schedule.filed_under(8, JOIN.force).rows == schedule.rows


def test_a_name_stamped_only_on_days_its_content_is_in_force_needs_no_refit():
    assert not refits(JOIN, [("v1", date(2026, 9, 3), date(2026, 9, 30)), ("v2", date(2026, 10, 1), date(2026, 10, 9))])


def test_a_name_stamped_on_a_day_another_content_is_in_force_is_refit():
    # v2 loaded the day before it took effect, and stamped that day's polls.
    assert refits(JOIN, [("v1", date(2026, 9, 3), date(2026, 9, 29)), ("v2", date(2026, 9, 30), date(2026, 10, 9))])


def test_rows_without_a_name_or_with_one_the_archive_does_not_map_are_refit_where_a_version_is_in_force():
    assert refits(JOIN, [("", date(2026, 10, 2), date(2026, 10, 2))])
    assert refits(JOIN, [("unmapped", date(2026, 10, 2), date(2026, 10, 2))])
    assert not refits(JOIN, [("", date(2027, 1, 2), date(2027, 1, 9))])


def test_a_name_of_the_same_content_as_the_version_in_force_needs_no_refit():
    renamed = JOIN._replace(stems=["v1", "v2", "v2_renamed"], hashes=["c1", "c2", "c2"])
    assert not refits(renamed, [("v2_renamed", date(2026, 10, 1), date(2026, 10, 9))])
