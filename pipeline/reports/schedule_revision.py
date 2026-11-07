"""Schedule-revision boundary dates: the first day a static GTFS feed
version is in force, keyed off `static_version_id`.

`agg_schedule_revision_daily` (built by `pipeline.analyze.analyze()`) stores
one row per (agency_id, date) holding that day's version: the one in force on
it (`pipeline.timetable_in_force`), or for a day no recorded version covers,
the DOMINANT static_version_id its rows were ingested against.
A day with no non-NULL static_version_id anywhere in `updates` (e.g. Aomori,
whose ingest strategy never joins static data, or any day before RT rows were
stamped with static_version_id) has NO ROW at all, never a NULL-version row.

A "boundary" is the first day, within a caller-given range, whose dominant
version differs from the immediately preceding CALENDAR day's — computed
here at read time from the stored per-day versions rather than persisted as
its own fact, so it stays correct even as new days get appended to the
table by later `analyze()` runs.

The same per-day read also backs the definition stamp's list of timetables in
force (`pipeline.reports.definition.stamp_build`).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple

# Every scope agency's recorded days, the day before the range included. An
# agency with none recorded in it is one row with no date, so it is still named.
_SQL = """
    SELECT a.agency_id, v.date, v.static_version_id
    FROM agencies a
    LEFT JOIN agg_schedule_revision_daily v ON v.agency_id = a.agency_id AND v.date BETWEEN $2 AND $3
    WHERE a.agency_id = $1::int OR ($1::int IS NULL AND a.deleted_at IS NULL)
    ORDER BY a.agency_id, v.date
"""


class ScheduleDays(NamedTuple):
    """Each day's dominant timetable over [from_date, to_date], plus the day
    before it, for one agency or every agency the board lists. One read serves
    both the definition stamp's timetables
    (:func:`pipeline.reports.definition.stamp_build`) and the trend chart's
    :meth:`boundaries`. *rows* are ``(agency_id, date, static_version_id)``,
    by agency then date; the date is None for an agency with none recorded."""

    from_date: date
    to_date: date
    rows: list

    def boundaries(self) -> list[str]:
        """ISO date strings in ``[from_date, to_date]`` where the static
        schedule version running that day differs from the previous CALENDAR
        day's of the same agency.

        A day whose immediately preceding calendar day has no row in
        `agg_schedule_revision_daily` at all (a gap — unknown version) is never
        flagged as a boundary against it: a gap means "unknown", not "known to
        be different". The day before from_date is read so a version change
        landing exactly on from_date is still detected against it, not just
        treated as the series' start.
        """
        boundaries: list[str] = []
        prev_agency: int | None = None
        prev_date: date | None = None
        prev_version: str | None = None
        for agency, d, version in self.rows:
            if agency != prev_agency:
                prev_agency, prev_date, prev_version = agency, None, None
            if d is None:
                continue
            if (
                prev_date is not None
                and d == prev_date + timedelta(days=1)
                and version != prev_version
                and d >= self.from_date
            ):
                boundaries.append(d.isoformat())
            prev_date, prev_version = d, version
        return boundaries


async def read_schedule_days(conn, agency_id: int | None, from_date: date, to_date: date) -> ScheduleDays:
    """The :class:`ScheduleDays` of *agency_id*, or of every agency the board
    lists when it is None."""
    rows = await conn.fetch(_SQL, agency_id, from_date - timedelta(days=1), to_date)
    return ScheduleDays(from_date, to_date, rows)
