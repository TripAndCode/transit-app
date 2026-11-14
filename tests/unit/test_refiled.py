"""A refiled date's rows are read from where analyze wrote them filed, never
by joining the schedule archive again, and only while what they were filed
against still holds (pipeline.refiled)."""

from datetime import date

import pytest

from pipeline import ch_aggregates, refiled, static_archive
from pipeline.live_filing import refiled_dates
from pipeline.refiled import Written
from pipeline.static_archive import ScheduleJoin, Stamp
from pipeline.timetable_in_force import Segment

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
JOIN = ScheduleJoin(["v1", "v2"], ["c1", "c2"], "", (Segment(D1, date(2026, 12, 31), "v1"),))
FILED = JOIN._replace(filed=((D1, "b1"), (D2, "b2")))


def test_a_filed_read_takes_its_refiled_dates_from_the_filed_rows_and_never_joins_the_archive():
    rows = static_archive.observation_rows("u.agency_id = {agency_id:UInt16}", Stamp.FILED)
    assert static_archive.SCHEDULE not in rows
    assert f"FROM {static_archive.FILED_TABLE} AS u" in rows
    assert "has({filed_builds:Array(String)}, u.build)" in rows
    # Every other date's rows are read from `updates` as IN_FORCE reads them.
    assert "NOT has({filed_dates:Array(Date)}, " in rows


def test_only_rows_of_updates_are_filed():
    with pytest.raises(ValueError, match="updates"):
        static_archive.observation_rows("1", Stamp.FILED, "updates_live")


def test_a_filed_read_binds_the_written_dates_it_reaches_and_their_builds():
    params = FILED.filing_params((D2, D3))
    assert (params["filed_dates"], params["filed_builds"]) == ([D2], ["b2"])
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


_WRITTEN = Written("b2", "v1", "c1", {"v1": "c1", "v2": "c2"}, 10)
_STAMPED = [("v1", D2, 6), ("v2", D2, 4)]


def test_written_rows_serve_a_date_whose_filing_still_holds():
    assert refiled.valid_builds(JOIN, {D2: _WRITTEN}, _STAMPED, {D2}) == {D2: "b2"}


@pytest.mark.parametrize(
    ("join", "written", "stamped"),
    [
        pytest.param(JOIN, {}, _STAMPED, id="never written"),
        pytest.param(JOIN, {D2: _WRITTEN._replace(build="")}, _STAMPED, id="written off"),
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
