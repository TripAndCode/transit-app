"""Async ops health checks for the admin dashboard.

These are async (asyncpg) counterparts to the sync CLI checks in
pipeline/freshness.py and db/migrate.py. Same SQL logic, different adapter —
the CLI keeps using the sync versions; the API calls these.
"""

import asyncio
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
    from api.clickhouse import max_captured_at_before
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
    # the table (measured 46.5s / 574M rows on real dev data) — worse than
    # even a per-agency full scan. `agencies` here is already filtered to
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
    # must not fail the whole call: degrade that agency's live_max to None and
    # keep going, same shape as pipeline.reports.network.compute_network_summary's
    # try/except around this same per-agency probe. is_stale(agg_day, None) is
    # "not stale" (see its docstring), which is the correct degrade here.
    async def _probe(aid: int) -> tuple[int, date | None]:
        try:
            mx = await max_captured_at_before(ch, aid, today_jst_midnight_utc)
        except Exception:
            _log.warning(
                "ClickHouse freshness probe failed for agency %s — degrading is_stale/data_to", aid, exc_info=True
            )
            return aid, None
        # max_captured_at_before always returns a tz-aware value (or None) —
        # no tzinfo-is-None fixup needed here, unlike the raw ClickHouse
        # driver return this helper wraps.
        return aid, None if mx is None else mx.astimezone(_JST).date()

    # Independent per-agency probes on the same async client — no reason to
    # pay N round trips serially (measured ~4s for 4 agencies) when they can
    # overlap. The per-agency try/except degrade above is preserved: gather
    # never raises here, each probe catches its own failure internally.
    live_max: dict[int, date | None] = dict(await asyncio.gather(*(_probe(a["agency_id"]) for a in agencies)))

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
