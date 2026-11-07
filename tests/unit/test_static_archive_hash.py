"""The archive's content key, the schedule join's parameters, and the day a
version name says its timetable was fetched."""

from datetime import date

import pytest

from pipeline.static_archive import ScheduleJoin, content_hash, fetch_day


def test_the_key_is_stable_for_the_same_parts():
    parts = ["static_stop_times:3:42", "static_trips:1:7", "static_routes:1:9"]
    assert content_hash(parts) == content_hash(list(parts))
    assert len(content_hash(parts)) == 32


def test_a_changed_part_changes_the_key():
    assert content_hash(["static_stop_times:3:42"]) != content_hash(["static_stop_times:3:43"])


def test_a_join_binds_every_mapped_content_and_the_loaded_one():
    join = ScheduleJoin(["s0", "s1"], ["h0", "h1"], "hc")
    assert join.params() == {
        "join_stems": ["s0", "s1"],
        "join_hashes": ["h0", "h1"],
        "join_current": "hc",
        "join_contents": ["h0", "h1", "hc"],
    }


def test_a_month_binds_only_the_contents_its_stems_join():
    join = ScheduleJoin(["s0", "s1"], ["h0", "h1"], "hc")
    assert join.params(["s1"])["join_contents"] == ["h1"]
    assert join.params(["s1", "", "gtfs_static_19990101"])["join_contents"] == ["h1", "hc"]


def test_with_no_loaded_timetable_unmapped_rows_join_nothing():
    assert ScheduleJoin(["s0"], ["h0"], "").params([""])["join_contents"] == []


def test_a_name_joins_its_mapped_content_else_the_loaded_one():
    content_of = ScheduleJoin(["s0", "s1"], ["h0", "h1"], "hc").content_of()
    assert [content_of(stem) for stem in ("s1", "", "gtfs_static_19990101")] == ["h1", "hc", "hc"]


@pytest.mark.parametrize(
    ("name", "day"),
    [
        ("gtfs_static_20260918", date(2026, 9, 18)),
        ("hiroden_static", None),
        ("gtfs_static_20261340", None),
        ("", None),
        (None, None),
    ],
)
def test_a_version_name_gives_the_day_its_zip_was_fetched(name, day):
    """The fetchers name each zip `gtfs_static_YYYYMMDD` after the day they
    fetched it; an older name carries no day, and a malformed one is no day."""
    assert fetch_day(name) == day
