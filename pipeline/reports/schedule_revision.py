"""Schedule-revision boundary dates: the day a static GTFS feed reload
becomes visible in the RT `updates` stream, keyed off `static_version_id`
(item 88).

`agg_schedule_revision_daily` (built by `pipeline.analyze.analyze()`) stores
one row per (agency_id, date) holding that day's DOMINANT static_version_id.
A day with no non-NULL static_version_id anywhere in `updates` (e.g. Aomori,
whose ingest strategy never joins static data, or any day predating item
88's rollout) has NO ROW at all, never a NULL-version row.

A "boundary" is the first day, within a caller-given range, whose dominant
version differs from the immediately preceding CALENDAR day's — computed
here at read time from the stored per-day versions rather than persisted as
its own fact, so it stays correct even as new days get appended to the
table by later `analyze()` runs.
"""

from __future__ import annotations

from datetime import date, timedelta

_SQL = """
    SELECT date, static_version_id
    FROM agg_schedule_revision_daily
    WHERE agency_id = $1 AND date BETWEEN $2 AND $3
    ORDER BY date
"""


async def get_schedule_revision_boundaries(conn, agency_id: int, from_date: date, to_date: date) -> list[str]:
    """ISO date strings in ``[from_date, to_date]`` where the static
    schedule version running that day differs from the previous CALENDAR
    day's.

    A day whose immediately preceding calendar day has no row in
    `agg_schedule_revision_daily` at all (a gap — unknown version) is never
    flagged as a boundary against it: a gap means "unknown", not "known to
    be different".
    """
    # One extra day fetched before from_date so a version change landing
    # exactly on from_date is still detected against the day before the
    # visible range, not just treated as the series' start.
    rows = await conn.fetch(_SQL, agency_id, from_date - timedelta(days=1), to_date)
    boundaries: list[str] = []
    prev_date: date | None = None
    prev_version: str | None = None
    for r in rows:
        d: date = r["date"]
        version: str = r["static_version_id"]
        if prev_date is not None and d == prev_date + timedelta(days=1) and version != prev_version and d >= from_date:
            boundaries.append(d.isoformat())
        prev_date, prev_version = d, version
    return boundaries
