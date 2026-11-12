"""The backfill's candidates: the names the archive does not map, oldest first, with their kept zips; and the
archived timetables whose kept headways --headways re-records."""

from datetime import date

from scripts.backfill_timetable_archive import Archived, Candidate, candidates, headway_candidates


def test_candidates_are_the_unmapped_names_oldest_first_with_their_zips(tmp_path):
    (tmp_path / "8").mkdir()
    (tmp_path / "8" / "gtfs_static_20261014.zip").write_bytes(b"")
    stems = {
        "": (10, date(2026, 7, 12), date(2026, 9, 30)),
        "gtfs_static_20261028": (5, date(2026, 10, 28), date(2026, 11, 2)),
        "gtfs_static_20261014": (7, date(2026, 7, 12), date(2026, 10, 24)),
        "gtfs_static_20260816": (3, date(2026, 9, 12), date(2026, 9, 16)),
    }
    found = candidates(stems, {"gtfs_static_20261028"}, tmp_path, 8)
    assert found == [
        Candidate(
            "gtfs_static_20261014",
            7,
            date(2026, 7, 12),
            date(2026, 10, 24),
            tmp_path / "8" / "gtfs_static_20261014.zip",
        ),
        Candidate("gtfs_static_20260816", 3, date(2026, 9, 12), date(2026, 9, 16), None),
    ]


def test_headway_candidates_group_each_content_with_its_names_and_kept_zips(tmp_path):
    (tmp_path / "8").mkdir()
    for stem in ("gtfs_static_20260601", "gtfs_static_20261014"):
        (tmp_path / "8" / f"{stem}.zip").write_bytes(b"")
    mapped = {
        "gtfs_static_20261014": "c2",
        "gtfs_static_20260601": "c1",
        "gtfs_static_20260715": "c1",
        "gtfs_static_20260901": "c3",
        "gtfs_static_20261101": "c4",
    }
    kept, other = headway_candidates(mapped, {"c4"}, tmp_path, 8)
    assert kept == [Archived("c4", ("gtfs_static_20261101",), ())]
    assert other == [
        Archived(
            "c1", ("gtfs_static_20260601", "gtfs_static_20260715"), (tmp_path / "8" / "gtfs_static_20260601.zip",)
        ),
        Archived("c3", ("gtfs_static_20260901",), ()),
        Archived("c2", ("gtfs_static_20261014",), (tmp_path / "8" / "gtfs_static_20261014.zip",)),
    ]
