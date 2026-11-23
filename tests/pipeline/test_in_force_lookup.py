"""ClickHouse places a service date among the segments in force as
ScheduleJoin.force_params binds them, and stays within memory however many
segments an agency's history holds."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta

import pytest

from pipeline.clickhouse import UPDATES_TABLE
from pipeline.static_archive import IN_FORCE_STEM_SQL, ScheduleJoin, _force_at
from pipeline.timetable_in_force import Segment

# Every day a ClickHouse Date holds, which FILED_DATE_SQL is.
_EVERY_DATE = "SELECT toDate('1970-01-01') + number AS d FROM numbers(65536)"
MEMORY_CAP = 100_000_000


def _join(force: list[Segment]) -> ScheduleJoin:
    stems = sorted({s.stem for s in force})
    return ScheduleJoin(stems, [f"h-{stem}" for stem in stems], "hc", tuple(force))


def _expected_place(force: list[Segment], day: date) -> int:
    at = bisect_right([s.first for s in force], day)
    return at if at and day <= force[at - 1].last else 0


def _days(first: date, count: int, length: int, gap: int = 0) -> list[Segment]:
    return [
        Segment(
            first + timedelta(days=i * (length + gap)),
            first + timedelta(days=i * (length + gap) + length - 1),
            f"v{i % 3}",
        )
        for i in range(count)
    ]


@pytest.mark.parametrize(
    "force",
    [
        [],
        [Segment(date(1900, 1, 1), date(2299, 12, 31), "v0")],
        [Segment(date(2026, 1, 1), date(2026, 1, 1), "v0"), Segment(date(2026, 1, 2), date(2026, 3, 1), "v1")],
        [
            Segment(date(1900, 1, 1), date(2025, 12, 31), "v0"),
            *_days(date(2026, 1, 1), 40, 3, gap=2),
            Segment(date(2026, 9, 1), date(2299, 12, 31), "v2"),
        ],
        _days(date(1969, 12, 20), 30, 5),
        _days(date(2149, 5, 1), 10, 7, gap=1),
        [
            Segment(date(1900, 1, 1), date(2025, 12, 31), "v0"),
            Segment(date(2026, 1, 1), date(2026, 1, 10), "v1"),
            Segment(date(2026, 1, 11), date(2140, 12, 31), "v0"),
            Segment(date(2141, 1, 5), date(2299, 12, 31), "v2"),
        ],
    ],
    ids=["none", "one-open", "two", "open-ends-with-gaps", "before-1970", "past-2149", "decades-long-interior"],
)
def test_every_date_falls_in_the_segment_that_covers_it(ch_client, force):
    rows = ch_client.query(
        f"SELECT d, {_force_at('d')} FROM ({_EVERY_DATE}) ORDER BY d", parameters=_join(force).force_params()
    ).result_rows
    assert len(rows) == 65536
    wrong = [(day, place) for day, place in rows if place != _expected_place(force, day)]
    assert wrong == []


def test_a_long_history_of_segments_fits_a_small_memory_cap(ch_client, agency_id):
    """A thousand one-day versions between two open-ended ones: a lambda
    matching each row against every segment's range copies the bound arrays
    per row and per thread, past the cap."""
    force = [
        Segment(date(1900, 1, 1), date(2024, 12, 31), "v0"),
        *_days(date(2025, 1, 1), 1000, 1, gap=1),
        Segment(date(2030, 7, 1), date(2299, 12, 31), "v1"),
    ]
    days, per_day = 2200, 100
    ch_client.command(
        f"INSERT INTO {UPDATES_TABLE} (agency_id, captured_at, file_name, trip_id, stop_sequence) "
        f"SELECT {agency_id}, "
        f"toDateTime64(toDateTime('2025-01-01 03:00:00', 'UTC') + (number % {days}) * 86400, 0, 'UTC'), "
        f"concat('live_', toString(number)), 'T1', 1 FROM numbers({days * per_day})"
    )
    result = ch_client.query(
        f"SELECT {IN_FORCE_STEM_SQL} AS stem, count() FROM {UPDATES_TABLE} AS u "
        "WHERE u.agency_id = {agency_id:UInt16} GROUP BY stem",
        parameters={"agency_id": agency_id, **_join(force).force_params()},
        settings={"max_memory_usage": MEMORY_CAP, "max_threads": 4},
    )
    expected: dict[str, int] = {}
    for offset in range(days):
        place = _expected_place(force, date(2025, 1, 1) + timedelta(days=offset))
        stem = force[place - 1].stem if place else ""
        expected[stem] = expected.get(stem, 0) + per_day
    assert dict(result.result_rows) == expected
