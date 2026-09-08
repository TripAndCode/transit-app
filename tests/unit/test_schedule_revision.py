"""Pure-logic tests for schedule-revision boundary detection.

No DB fixtures -- `get_schedule_revision_boundaries` only calls
`conn.fetch(sql, ...)` once, so a fake connection returning canned rows
exercises the boundary-detection algorithm directly.
"""

from datetime import date

from pipeline.reports.schedule_revision import get_schedule_revision_boundaries


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, _sql, *_args):
        return self._rows


def _row(d: str, version: str) -> dict:
    return {"date": date.fromisoformat(d), "static_version_id": version}


async def test_no_boundary_when_version_is_constant():
    rows = [_row("2026-05-01", "v1"), _row("2026-05-02", "v1"), _row("2026-05-03", "v1")]
    conn = _FakeConn(rows)
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == []


async def test_boundary_on_first_day_version_differs_from_previous_calendar_day():
    rows = [_row("2026-05-01", "v1"), _row("2026-05-02", "v1"), _row("2026-05-03", "v2")]
    conn = _FakeConn(rows)
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == ["2026-05-03"]


async def test_boundary_landing_exactly_on_from_date_is_still_detected():
    """The function fetches one extra day before from_date specifically so a
    version change landing exactly on the visible range's first day is still
    flagged, not treated as the series' unremarkable start."""
    rows = [_row("2026-04-30", "v1"), _row("2026-05-01", "v2"), _row("2026-05-02", "v2")]
    conn = _FakeConn(rows)
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 2))
    assert boundaries == ["2026-05-01"]


async def test_gap_before_a_different_version_is_not_flagged_as_a_boundary():
    """A missing day right before a version change means "unknown", not
    "known to be different" -- no row exists for 2026-05-02, so 2026-05-03's
    'v2' must not be reported as a boundary against the day before it."""
    rows = [_row("2026-05-01", "v1"), _row("2026-05-03", "v2")]
    conn = _FakeConn(rows)
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 3))
    assert boundaries == []


async def test_multiple_boundaries_in_one_range():
    rows = [
        _row("2026-05-01", "v1"),
        _row("2026-05-02", "v2"),
        _row("2026-05-03", "v2"),
        _row("2026-05-04", "v3"),
    ]
    conn = _FakeConn(rows)
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 4))
    assert boundaries == ["2026-05-02", "2026-05-04"]


async def test_empty_result_when_no_rows_at_all():
    conn = _FakeConn([])
    boundaries = await get_schedule_revision_boundaries(conn, 1, date(2026, 5, 1), date(2026, 5, 4))
    assert boundaries == []
