"""The backfill's candidates: the names the archive does not map, oldest first, with their kept zips."""

from datetime import date

from scripts.backfill_timetable_archive import Candidate, candidates


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
