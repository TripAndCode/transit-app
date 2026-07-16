"""Async ops health checks for the admin dashboard.

These are async (asyncpg) counterparts to the sync CLI checks in
pipeline/freshness.py and db/migrate.py. Same SQL logic, different adapter —
the CLI keeps using the sync versions; the API calls these.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from db.migrate import _versions_on_disk

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

# LATERAL per agency (not a bare GROUP BY over all of `updates`) so each probe
# is an agency_id-equality + MAX — an index-backed backward scan on
# idx_updates_agency_at — instead of a full scan/aggregate of the whole
# fact table. Only active agencies are probed (WHERE deleted_at IS NULL).
_LIVE_MAX_SQL = """
    SELECT a.agency_id, (m.mx AT TIME ZONE 'Asia/Tokyo')::date AS d
    FROM agencies a
    CROSS JOIN LATERAL (
        SELECT MAX(u.captured_at) AS mx
        FROM updates u
        WHERE u.agency_id = a.agency_id
          AND u.captured_at < (date_trunc('day', now() AT TIME ZONE 'Asia/Tokyo'))
                              AT TIME ZONE 'Asia/Tokyo'
    ) m
    WHERE a.deleted_at IS NULL
"""

_FEED_HEALTH_SQL = """
    SELECT agency_id, SUM(raw_samples) AS raw, SUM(clamp_count) AS clamp
    FROM agg_feed_health
    WHERE date >= (now() AT TIME ZONE 'Asia/Tokyo')::date - 30
    GROUP BY agency_id
"""


async def aggregate_freshness(conn: asyncpg.Connection) -> list[AgencyFreshness]:
    """Per-agency freshness using agg_meta, agg_route_daily, and updates."""
    from pipeline.freshness import is_stale

    now_utc = datetime.now(timezone.utc)
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    meta_rows = await conn.fetch("SELECT agency_id, analyzed_at FROM agg_meta")
    meta = {r["agency_id"]: r["analyzed_at"] for r in meta_rows}
    agg_max_rows = await conn.fetch(_AGG_MAX_SQL)
    agg_max = {r["agency_id"]: r["d"] for r in agg_max_rows}
    live_max_rows = await conn.fetch(_LIVE_MAX_SQL)
    live_max = {r["agency_id"]: r["d"] for r in live_max_rows}

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
            behind = 1
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
