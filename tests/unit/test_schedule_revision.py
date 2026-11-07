"""Pure-logic tests for schedule-revision boundary detection.

No DB fixtures -- `read_schedule_days` only calls `conn.fetch(sql, ...)`
once, so a fake connection returning canned rows exercises the
boundary-detection algorithm directly.
"""

from datetime import date

from pipeline.reports.schedule_revision import ScheduleDays, read_schedule_days


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple] = []

    async def fetch(self, _sql, *args):
        self.calls.append(args)
        return self._rows


def _row(d: str, version: str) -> tuple:
    return (1, date.fromisoformat(d), version)


async def _boundaries(conn, agency_id, from_date, to_date):
    return (await read_schedule_days(conn, agency_id, from_date, to_date)).boundaries()


async def test_no_boundary_when_version_is_constant():
    rows = [_row("2026-05-01", "v1"), _row("2026-05-02", "v1"), _row("2026-05-03", "v1")]
    conn = _FakeConn(rows)
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == []


async def test_boundary_on_first_day_version_differs_from_previous_calendar_day():
    rows = [_row("2026-05-01", "v1"), _row("2026-05-02", "v1"), _row("2026-05-03", "v2")]
    conn = _FakeConn(rows)
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == ["2026-05-03"]


async def test_boundary_landing_exactly_on_from_date_is_still_detected():
    """The function fetches one extra day before from_date specifically so a
    version change landing exactly on the visible range's first day is still
    flagged, not treated as the series' unremarkable start."""
    rows = [_row("2026-04-30", "v1"), _row("2026-05-01", "v2"), _row("2026-05-02", "v2")]
    conn = _FakeConn(rows)
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 2))
    assert boundaries == ["2026-05-01"]


async def test_gap_before_a_different_version_is_not_flagged_as_a_boundary():
    """A missing day right before a version change means "unknown", not
    "known to be different" -- no row exists for 2026-05-02, so 2026-05-03's
    'v2' must not be reported as a boundary against the day before it."""
    rows = [_row("2026-05-01", "v1"), _row("2026-05-03", "v2")]
    conn = _FakeConn(rows)
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == []


async def test_multiple_boundaries_in_one_range():
    rows = [
        _row("2026-05-01", "v1"),
        _row("2026-05-02", "v2"),
        _row("2026-05-03", "v2"),
        _row("2026-05-04", "v3"),
    ]
    conn = _FakeConn(rows)
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 4))
    assert boundaries == ["2026-05-02", "2026-05-04"]


async def test_empty_result_when_no_rows_at_all():
    conn = _FakeConn([])
    boundaries = await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 4))
    assert boundaries == []


async def test_an_agency_with_no_recorded_day_has_no_boundary():
    conn = _FakeConn([(1, None, None)])
    assert await _boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 4)) == []


async def test_the_day_before_the_range_is_read():
    conn = _FakeConn([])
    await read_schedule_days(conn, 1, date(2026, 5, 1), date(2026, 5, 4))
    assert conn.calls == [(1, date(2026, 4, 30), date(2026, 5, 4))]


def test_one_agencys_last_day_is_never_compared_with_the_next_agencys_first():
    """A board-wide read holds several agencies' days in a row; a change of
    agency is not a timetable revision."""
    rows = [
        (1, date(2026, 5, 1), "a1"),
        (1, date(2026, 5, 2), "a1"),
        (2, date(2026, 5, 3), "b1"),
        (2, date(2026, 5, 4), "b2"),
    ]
    schedule = ScheduleDays(date(2026, 5, 1), date(2026, 5, 4), rows)
    assert schedule.boundaries() == ["2026-05-04"]
