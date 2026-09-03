"""Async ops health checks for the admin dashboard.

These are async (asyncpg) counterparts to the sync CLI checks in
pipeline/freshness.py and db/migrate.py. Same SQL logic, different adapter —
the CLI keeps using the sync versions; the API calls these.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import asyncpg

from db.migrate import _versions_on_disk

_log = logging.getLogger(__name__)

# ── Migration status ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class MigrationStatus:
    applied: str | None  # latest applied version string, e.g. "0025"
    latest: str | None  # latest on-disk version string
    behind: int  # count of on-disk versions not in schema_migrations


async def migration_status(conn: asyncpg.Connection) -> MigrationStatus:
    """Return migration status by comparing on-disk files against schema_migrations."""
    on_disk = _versions_on_disk()
    latest = on_disk[-1] if on_disk else None
    try:
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        applied_set = {r["version"] for r in rows}
        pending = [v for v in on_disk if v not in applied_set]
        applied_sorted = sorted(applied_set)
        applied_latest = applied_sorted[-1] if applied_sorted else None
    except asyncpg.UndefinedTableError:
        # schema_migrations table doesn't exist yet
        pending = list(on_disk)
        applied_latest = None
    return MigrationStatus(applied=applied_latest, latest=latest, behind=len(pending))


# ── Aggregate freshness ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AgencyFreshness:
    agency_id: int
    agency_name: str
    last_analyzed_at: datetime | None
    analyze_age_hours: float | None
    agg_fresh: bool
    agg_behind_days: int
    is_stale: bool
    data_to: str | None
    clamp_pct: float | None


_AGG_MAX_SQL = "SELECT agency_id, MAX(date) AS d FROM agg_route_daily GROUP BY agency_id"

_FEED_HEALTH_SQL = """
    SELECT agency_id, SUM(raw_samples) AS raw, SUM(clamp_count) AS clamp
    FROM agg_feed_health
    WHERE date >= (now() AT TIME ZONE 'Asia/Tokyo')::date - 30
    GROUP BY agency_id
"""

_JST = ZoneInfo("Asia/Tokyo")


async def aggregate_freshness(conn: asyncpg.Connection, ch) -> list[AgencyFreshness]:
    """Per-agency freshness using agg_meta, agg_route_daily, and ClickHouse `updates`."""
    from api.clickhouse import max_captured_at_before_by_agency, min_captured_at_by_agency
    from pipeline.freshness import is_stale

    now_utc = datetime.now(timezone.utc)
    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    )
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    meta_rows = await conn.fetch("SELECT agency_id, analyzed_at FROM agg_meta")
    meta = {r["agency_id"]: r["analyzed_at"] for r in meta_rows}
    agg_max_rows = await conn.fetch(_AGG_MAX_SQL)
    agg_max = {r["agency_id"]: r["d"] for r in agg_max_rows}

    # One indexed read per agency (`api.clickhouse.max_captured_at_before` —
    # see its docstring) instead of an unfiltered `GROUP BY agency_id` over
    # the whole `updates` table: the GROUP BY form has no `agency_id`
    # predicate at all, so it reads the `captured_at` column for every row in
    # the table — worse than even a per-agency full scan, and it gets worse
    # as the table grows. `agencies` here is already filtered to
    # non-deleted agencies (the query above), so no separate active-id filter
    # is needed. The "only a COMPLETED day counts" cutoff is baked into the
    # helper's `before` predicate (see its docstring and `pipeline.freshness`'s
    # module docstring) — filtering BEFORE taking the max, not a Python-side
    # accept/reject of an unconditional max, which would silently produce
    # "no completed day" for any agency ingesting today too (the normal,
    # healthy, continuously-ingesting case).
    #
    # This probe backs ONLY the CH-derived fields below (is_stale/data_to/
    # agg_behind_days) — every other field (last_analyzed_at, analyze_age_hours,
    # clamp_pct) comes from Postgres. So a ClickHouse hiccup for ONE agency
    # must not fail the whole call: is_stale(agg_day, None) is "not stale"
    # (see its docstring), which is the correct degrade here.
    # max_captured_at_before_by_agency runs the per-agency probes concurrently
    # and degrades a failing agency's probe to None internally; shared with
    # pipeline.reports.network.compute_network_summary's identical shape.
    probed = await max_captured_at_before_by_agency(
        ch, [a["agency_id"] for a in agencies], today_jst_midnight_utc, _log
    )
    live_max: dict[int, date | None] = {
        aid: None if mx is None else mx.astimezone(_JST).date() for aid, mx in probed.items()
    }

    # A never-analyzed agency (agg_day is None) needs the earliest live day
    # too, to size agg_behind_days off the real unaggregated span instead of
    # a flat placeholder. Only probed for that (normally rare) subset —
    # every other agency already has everything it needs from agg_max/live_max
    # above, so this never adds a query for the common already-analyzed case.
    never_analyzed_ids = [
        a["agency_id"]
        for a in agencies
        if agg_max.get(a["agency_id"]) is None and live_max.get(a["agency_id"]) is not None
    ]
    earliest_live: dict[int, date | None] = {}
    if never_analyzed_ids:
        probed_earliest = await min_captured_at_by_agency(ch, never_analyzed_ids, _log)
        earliest_live = {aid: None if mn is None else mn.astimezone(_JST).date() for aid, mn in probed_earliest.items()}

    # agg_feed_health may not exist in all environments — degrade gracefully
    try:
        feed_rows = await conn.fetch(_FEED_HEALTH_SQL)
        feed = {r["agency_id"]: r for r in feed_rows}
    except asyncpg.UndefinedTableError:
        feed = {}

    result: list[AgencyFreshness] = []
    for a in agencies:
        aid = a["agency_id"]
        analyzed_at = meta.get(aid)
        if analyzed_at is not None:
            age_hours = (now_utc - analyzed_at).total_seconds() / 3600
        else:
            age_hours = None

        agg_day = agg_max.get(aid)
        live_day = live_max.get(aid)

        stale = is_stale(agg_day, live_day)

        if live_day is None:
            behind = 0
        elif agg_day is None:
            earliest_day = earliest_live.get(aid)
            # Inclusive day count from the earliest unaggregated day through
            # the newest completed one — e.g. a single day of unaggregated
            # data (earliest_day == live_day) is 1 day behind, not 0. Falls
            # back to the old flat placeholder only if the earliest-day probe
            # itself degraded to None (e.g. a ClickHouse hiccup for this one
            # agency), matching this function's usual "degrade, don't fail
            # the whole call" shape.
            behind = (live_day - earliest_day).days + 1 if earliest_day is not None else 1
        else:
            behind = max(0, (live_day - agg_day).days)

        data_to = live_day.isoformat() if live_day else None

        f = feed.get(aid)
        raw = int(f["raw"]) if f and f["raw"] else 0
        clamp = int(f["clamp"]) if f and f["clamp"] else 0
        clamp_pct = round(clamp / raw * 100, 2) if raw else None

        result.append(
            AgencyFreshness(
                agency_id=aid,
                agency_name=a["agency_name"],
                last_analyzed_at=analyzed_at,
                analyze_age_hours=round(age_hours, 2) if age_hours is not None else None,
                agg_fresh=not stale,
                agg_behind_days=behind,
                is_stale=stale,
                data_to=data_to,
                clamp_pct=clamp_pct,
            )
        )
    return result
