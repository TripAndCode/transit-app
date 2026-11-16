"""Which JST days a folder of collector tarballs covers, and which of them the
retention floor leaves replaceable (pipeline.archive_replace)."""

import pathlib
from datetime import date

from pipeline.archive_replace import above_floor, covered_days, tarball_days
from pipeline.freshness import day_ranges


def test_a_jst_day_is_covered_by_its_own_and_the_previous_utc_days_tarballs():
    stems = {date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 4)}
    assert covered_days(stems, today=date(2026, 5, 1)) == {date(2026, 4, 2)}


def test_a_day_that_has_not_closed_is_never_covered():
    stems = {date(2026, 4, 1), date(2026, 4, 2)}
    assert covered_days(stems, today=date(2026, 4, 2)) == set()


def test_tarball_days_reads_dated_names_and_skips_the_rest():
    names = ["20260401.tar.gz", "20260431.tar.gz", "backup.tgz", "20260402.tgz"]
    assert tarball_days([pathlib.Path(n) for n in names]) == {date(2026, 4, 1), date(2026, 4, 2)}


def test_the_floor_day_itself_is_final_because_its_small_hours_file_under_the_day_before():
    floor = date(2026, 4, 1)
    assert not above_floor(floor, floor)
    assert above_floor(date(2026, 4, 2), floor)
    assert above_floor(floor, None)


def test_day_ranges_joins_consecutive_days():
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 7)]
    assert day_ranges(days) == "2026-10-01..2026-10-03, 2026-10-07"
