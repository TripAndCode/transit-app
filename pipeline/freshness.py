"""Detect stale or partially-built aggregate tables.

Read endpoints serve precomputed agg_* tables, never live scans, so an agency
whose analyze never ran (or whose cron loop crashed mid-run) silently serves
stale data. This module compares, per agency, the newest COMPLETED civil day in
the live `updates` fact table against the newest day materialised in
`agg_route_daily` — the canonical always-built agg. Today's partial day is
excluded so the continuously-lagging current day never trips the check.

The newest-completed-day cutoff (JST midnight, converted to UTC) is computed
in Python, then passed as a `WHERE captured_at < {before}` bound into the
ClickHouse query itself (`pipeline.clickhouse.max_captured_at_before`) —
filtering BEFORE taking the max, not calling the unconditional
`max_captured_at` and rejecting the result in Python afterwards. The latter
would return "no completed day" whenever a later (e.g. today's,
still-ingesting) row also exists, instead of falling back to the latest
prior completed day — silently defeating staleness detection under the
normal, continuously-ingesting operating condition. The cutoff itself still
has to be computed in Python since the live `updates` table lives in
ClickHouse (`DateTime64(0, 'UTC')`) while `agg_route_daily` stays in
Postgres, bucketed under Asia/Tokyo by analyze() — comparing a
Python-computed cutoff against ClickHouse's stored UTC instants keeps this
correct regardless of the caller's session timezone. (analyze must still
bucket under JST for the comparison to hold — every analyze path, including
the cron task, pins Asia/Tokyo on its connection.)

Only the newest completed day is compared, which catches the realistic failure
modes (cron crashed mid-loop, forgot to re-analyze). It does NOT detect an
interior missing day — analyze's atomic per-agency wipe-and-rewrite cannot
produce one, so a mid-range gap would only arise from manual row surgery.

`check_feed_freshness`/`feed_is_stale` below answer a different question with
the same day-granularity rule (`is_stale`, reused directly): not whether the
MATERIALIZED aggregates lag the live fact table, but whether the feed ITSELF
has stopped ticking forward, via its own self-reported `feed_timestamp` (the
GTFS-RT `FeedHeader.timestamp`, populated per agency — see
`pipeline.clickhouse.UPDATE_COLUMNS`). Reusing `is_stale` for both means a
feed that has gone fully silent since before today's JST midnight is flagged
exactly the same way an agg-lag is: this coarse, whole-day-boundary signal
does not catch a feed frozen for a few hours WITHIN the current day (the same
limitation `is_stale` already has for the aggregate case above).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from pipeline.clickhouse import latest_feed_timestamp as ch_latest_feed_timestamp
from pipeline.clickhouse import max_captured_at_before as ch_max_captured_at_before

_JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class StaleAgency:
    agency_id: int
    agg_max_day: date | None
    live_max_completed_day: date | None


def is_stale(agg_max_day: date | None, live_max_completed_day: date | None) -> bool:
    """Pure staleness rule. Stale iff a completed day is owed but uncovered.

    - No completed days yet (live_max_completed_day is None): nothing owed → fresh.
    - Aggs empty (agg_max_day is None) but a completed day exists: stale.
    - Aggs lag the newest completed day: stale.
    """
    if live_max_completed_day is None:
        return False
    if agg_max_day is None:
        return True
    return agg_max_day < live_max_completed_day


_AGG_MAX_SQL = "SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s"


def check_agg_freshness(conn, ch_client, agency_ids: Iterable[int]) -> list[StaleAgency]:
    """Return agencies whose aggregates lag their newest completed civil day.

    Read-only. Empty list means every agency is fresh.
    """
    stale: list[StaleAgency] = []
    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    )
    with conn.cursor() as cur:
        for aid in agency_ids:
            latest = ch_max_captured_at_before(ch_client, aid, today_jst_midnight_utc)
            live_max = latest.astimezone(_JST).date() if latest is not None else None
            cur.execute(_AGG_MAX_SQL, (aid,))
            agg_max = cur.fetchone()[0]
            if is_stale(agg_max, live_max):
                stale.append(StaleAgency(aid, agg_max, live_max))
    return stale


@dataclass(frozen=True)
class FeedFreshness:
    agency_id: int
    feed_timestamp: datetime | None
    age_seconds: float | None
    is_stale: bool


def feed_is_stale(now: datetime, feed_timestamp: datetime | None) -> bool:
    """Whether an agency's GTFS-RT feed has fallen behind, per its own
    self-reported `feed_timestamp` (see the module docstring's "different
    question" note) — reuses `is_stale`'s exact day-granularity comparison
    (JST calendar day) so this can never disagree with the aggregate-lag
    rule above about what "behind" means.

    `feed_timestamp is None` means the field is unpopulated for this
    agency's ingest strategy, or the agency has no rows at all — a
    structural gap, not evidence the feed is unhealthy — so this resolves
    to "not stale" (nothing to judge), unlike `is_stale`'s own
    `agg_max_day=None` branch, which means something different (a completed
    day IS owed but genuinely missing).
    """
    if feed_timestamp is None:
        return False
    return is_stale(feed_timestamp.astimezone(_JST).date(), now.astimezone(_JST).date())


def check_feed_freshness(conn, ch_client, agency_ids: Iterable[int]) -> list[FeedFreshness]:
    """Per-agency `(now - feed_timestamp)` staleness — see `feed_is_stale`.

    Read-only. `conn` is accepted (unused) for signature parity with
    `check_agg_freshness`, so a future caller that wants to run both checks
    together can do so uniformly.
    """
    del conn
    now = datetime.now(timezone.utc)
    out: list[FeedFreshness] = []
    for aid in agency_ids:
        ts = ch_latest_feed_timestamp(ch_client, aid)
        age = None if ts is None else (now - ts).total_seconds()
        out.append(FeedFreshness(agency_id=aid, feed_timestamp=ts, age_seconds=age, is_stale=feed_is_stale(now, ts)))
    return out
