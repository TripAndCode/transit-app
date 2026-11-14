"""A refiled date's rows are read from where analyze wrote them filed, never
by joining the schedule archive again, and only while what they were filed
against still holds (pipeline.refiled)."""

import hashlib
from datetime import date, timedelta

import pytest

from pipeline import ch_aggregates, refiled, static_archive
from pipeline.live_filing import refiled_dates
from pipeline.refiled import Written
from pipeline.service_day import filed_date_of
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.timetable_in_force import Segment

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
JOIN = ScheduleJoin(["v1", "v2"], ["c1", "c2"], "", (Segment(D1, date(2026, 12, 31), "v1"),))
FILED = JOIN._replace(filed=((D1, "b1"), (D2, "b2")))


def test_a_filed_read_takes_its_refiled_dates_from_the_filed_rows_and_never_joins_the_archive():
    rows = static_archive.observation_rows("u.agency_id = {agency_id:UInt16}", Stamp.FILED)
    assert static_archive.SCHEDULE not in rows
    assert f"FROM {static_archive.FILED_TABLE} AS u" in rows
    assert "u.build IN {filed_builds:Array(String)}" in rows
    # Every other date's rows are read from `updates` as IN_FORCE reads them.
    assert f"{static_archive.FILED_DATE_SQL} NOT IN {{filed_dates:Array(Date)}}" in rows


def test_only_rows_of_updates_are_filed():
    with pytest.raises(ValueError, match="updates"):
        static_archive.observation_rows("1", Stamp.FILED, "updates_live")


def test_a_filed_read_binds_the_written_dates_it_reaches_their_builds_and_their_range():
    params = FILED.filing_params((D2, D3))
    assert (params["filed_dates"], params["filed_builds"]) == ([D2], ["b2"])
    assert (params["filed_first"], params["filed_last"]) == (D2, D2)
    rows = static_archive.observation_rows("1", Stamp.FILED)
    # The sort key and the partitions prune on the range.
    assert "u.filed_date BETWEEN {filed_first:Date} AND {filed_last:Date}" in rows
    assert "filed_dates" not in JOIN.filing_params((D2, D3))


def test_a_read_is_filed_only_when_every_refiled_date_it_reaches_was_written():
    assert ch_aggregates.stamp_for(FILED, {D1, D2}, [D1, D2, D3]) is Stamp.FILED
    assert ch_aggregates.stamp_for(FILED, {D2, D3}, [D1, D2, D3]) is Stamp.REFIT
    assert ch_aggregates.stamp_for(FILED, {D3}, [D1, D2]) is Stamp.IN_FORCE
    assert ch_aggregates.stamp_for(FILED, None, [D1]) is Stamp.REFIT


def test_filed_and_refit_reads_both_rewrite_the_schedule():
    assert Stamp.FILED.refiles and Stamp.REFIT.refiles
    assert not Stamp.IN_FORCE.refiles and not Stamp.INGESTED.refiles


def test_the_live_decision_names_each_date_a_row_of_which_is_refiled():
    # v2 stamped D2's polls while v1 was in force; '' is a poll no timetable matched.
    stamped = [("v1", D1, 10), ("v2", D2, 4), ("v1", D2, 6), ("", D3, 1)]
    assert refiled_dates(JOIN, stamped) == {D2, D3}


_WRITTEN = Written("b2", "v1", "c1", {"v1": "c1", "v2": "c2"}, 10, refiled.RULES)
_STAMPED = [("v1", D2, 6), ("v2", D2, 4)]


def test_the_filing_rules_are_the_text_of_the_write():
    assert refiled.RULES == hashlib.sha256(static_archive.refiled_rows_sql("{where}").encode()).hexdigest()[:16]


def test_written_rows_serve_a_date_whose_filing_still_holds():
    assert refiled.valid_builds(JOIN, {D2: _WRITTEN}, _STAMPED, {D2}) == {D2: "b2"}


@pytest.mark.parametrize(
    ("join", "written", "stamped"),
    [
        pytest.param(JOIN, {}, _STAMPED, id="never written"),
        pytest.param(JOIN, {D2: _WRITTEN._replace(build="")}, _STAMPED, id="written off"),
        pytest.param(JOIN, {D2: _WRITTEN._replace(rules="other")}, _STAMPED, id="written under other rules"),
        pytest.param(
            JOIN._replace(force=(Segment(D1, date(2026, 12, 31), "v2"),)), {D2: _WRITTEN}, _STAMPED, id="in force moved"
        ),
        pytest.param(JOIN._replace(hashes=["c1", "c9"]), {D2: _WRITTEN}, _STAMPED, id="a name remapped"),
        pytest.param(JOIN, {D2: _WRITTEN}, [("v1", D2, 6), ("v2", D2, 5)], id="rows landed since"),
        pytest.param(JOIN, {D2: _WRITTEN}, [("v1", D2, 6), ("v3", D2, 4)], id="a name it never saw"),
    ],
)
def test_written_rows_are_never_read_once_their_filing_can_have_moved(join, written, stamped):
    assert refiled.valid_builds(join, written, stamped, {D2}) is None


def test_a_filed_read_skips_by_capture_each_run_of_written_dates_less_the_filing_reach():
    from datetime import timedelta

    from pipeline.service_day import FILING_REACH_HOURS, service_day_start_utc
    from pipeline.static_archive import FILED_SKIP_RUNS

    reach = timedelta(hours=FILING_REACH_HOURS)
    d5, d6, d9 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 9)
    written = JOIN._replace(filed=tuple((day, "b") for day in (D1, d5, d6, d9)))
    params = written.filing_params((D1, d9))
    skips = [(params[f"filed_skip_from_{i}"], params[f"filed_skip_to_{i}"]) for i in range(FILED_SKIP_RUNS)]
    # A run of two days first, then each single day; a reading captured
    # within the reach of a run's edge can file under the day beside it.
    assert skips[:3] == [
        (service_day_start_utc(d5) + reach, service_day_start_utc(d6 + timedelta(days=1)) - reach),
        (service_day_start_utc(D1) + reach, service_day_start_utc(D2) - reach),
        (service_day_start_utc(d9) + reach, service_day_start_utc(d9 + timedelta(days=1)) - reach),
    ]
    assert all(start == end for start, end in skips[3:])


def test_a_filed_read_skips_the_longest_runs_when_there_are_more_than_it_has_slots_for():
    from datetime import timedelta

    from pipeline.static_archive import FILED_SKIP_RUNS

    singles = [D1 + timedelta(days=2 * i) for i in range(FILED_SKIP_RUNS + 2)]
    pair = [singles[-1] + timedelta(days=5), singles[-1] + timedelta(days=6)]
    written = JOIN._replace(filed=tuple((day, "b") for day in (*singles, *pair)))
    params = written.filing_params((D1, pair[-1]))
    widths = [params[f"filed_skip_to_{i}"] - params[f"filed_skip_from_{i}"] for i in range(FILED_SKIP_RUNS)]
    assert widths[0] > widths[1] == widths[-1] > timedelta(0)


def test_a_filed_read_reads_updates_only_where_a_date_it_reaches_is_unwritten():
    assert FILED.filing_params((D1, D2))["filed_rest"] == 0
    assert FILED.filing_params((D1, D3))["filed_rest"] == 1
    assert FILED.params()["filed_rest"] == 1
    assert "{filed_rest:UInt8} = 1" in static_archive.observation_rows("1", Stamp.FILED)


def test_a_reading_captured_in_a_skipped_span_files_under_its_run_below_the_offset_bound():
    """The capture skip is exact for scheduled offsets under
    MAX_EXACT_SKIP_OFFSET_SEC, and only for those."""
    run = [D1, D2]
    start, end = static_archive._capture_skips(run)[0]
    misfiled_under, misfiled_over = 0, 0
    captured = start
    while captured < end:
        for offset in range(0, 50 * 3600, 600):
            if filed_date_of(captured, offset) not in run:
                if offset < static_archive.MAX_EXACT_SKIP_OFFSET_SEC:
                    misfiled_under += 1
                else:
                    misfiled_over += 1
        captured += timedelta(minutes=10)
    assert misfiled_under == 0
    assert misfiled_over > 0
