"""Detect stale or partially-built aggregate tables.

Read endpoints serve precomputed agg_* tables, never live scans, so an agency
whose analyze never ran (or whose cron loop crashed mid-run) silently serves
stale data. This module compares, per agency, the newest COMPLETED civil day in
the live `updates` fact table against the newest day materialised in
`agg_route_daily` — the canonical always-built agg. Today's partial day is
excluded so the continuously-lagging current day never trips the check.

The newest-completed-day cutoff (JST midnight, converted to UTC) is computed
in Python, then passed as a `WHERE captured_at < {before}` bound into the
ClickHouse query itself (`pipeline.clickhouse.newest_filed_date_before`) —
filtering BEFORE taking the max, not calling the unconditional
`max_captured_at` and rejecting the result in Python afterwards. The latter
would return "no completed day" whenever a later (e.g. today's,
still-ingesting) row also exists, instead of falling back to the latest
prior completed day — silently defeating staleness detection under the
normal, continuously-ingesting operating condition. The cutoff itself still
has to be computed in Python since the live `updates` table lives in
ClickHouse (`DateTime64(0, 'UTC')`) while `agg_route_daily` stays in
Postgres, filed by service date by analyze() — comparing a
Python-computed cutoff against ClickHouse's stored UTC instants keeps this
correct regardless of the caller's session timezone. The live side is the
service date the newest readings file a stop event under
(`pipeline.service_day.filed_date_ch`), the same day analyze files each stop
event under, so the two sides compare like with like.

Only the newest completed day is compared, which catches the realistic failure
modes (cron crashed mid-loop, forgot to re-analyze). It does NOT detect an
interior missing day — analyze rebuilds every date whose source rows changed
in one transaction, so a mid-range gap would only arise from manual row
surgery or from a stale interior date the ledger cannot see (see
`pipeline.analyze._dates_needing_rebuild`).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pipeline.clickhouse import LIVE_SOURCED, jst_date, jst_midnight_utc, majority_source, newest_filed_date_before
from pipeline.retention import retention_floors

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
            live_max = newest_filed_date_before(ch_client, aid, today_jst_midnight_utc)
            cur.execute(_AGG_MAX_SQL, (aid,))
            agg_max = cur.fetchone()[0]
            if is_stale(agg_max, live_max):
                stale.append(StaleAgency(aid, agg_max, live_max))
    return stale


@dataclass(frozen=True)
class LiveSourcedDays:
    """An agency's analyzed dates whose rows came mostly from the live path
    (pipeline.clickhouse.majority_source): *open* after its retention floor,
    which an archive ingest can still replace (pipeline.archive_replace), and
    how many *final* ones lie on or before it."""

    agency_id: int
    open: list[date]
    final: int


_SOURCE_COUNTS_SQL = (
    "SELECT agency_id, date, archive_rows, live_rows FROM agg_feed_health "
    "WHERE agency_id = ANY(%s) ORDER BY agency_id, date"
)


def live_sourced_days(conn, agency_ids: Iterable[int]) -> list[LiveSourcedDays]:
    """Each agency with a live-sourced date in agg_feed_health. Read-only."""
    ids = sorted(set(agency_ids))
    floors = retention_floors(conn, ids)
    by_agency: dict[int, list[date]] = {}
    with conn.cursor() as cur:
        cur.execute(_SOURCE_COUNTS_SQL, (ids,))
        for agency_id, day, archive_rows, live_rows in cur.fetchall():
            if majority_source(archive_rows, live_rows) == "live":
                by_agency.setdefault(agency_id, []).append(day)
    found = []
    for agency_id, days in by_agency.items():
        floor = floors.get(agency_id)
        open_days = [day for day in days if floor is None or day > floor]
        found.append(LiveSourcedDays(agency_id, open_days, len(days) - len(open_days)))
    return found


def day_ranges(days: list[date]) -> str:
    """*days* (ascending) as comma-separated runs: ``2026-10-01..2026-10-03, 2026-10-07``."""
    runs: list[list[date]] = []
    for day in days:
        if runs and day - runs[-1][-1] == timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    return ", ".join(str(run[0]) if len(run) == 1 else f"{run[0]}..{run[-1]}" for run in runs)


# Service dates whose ledger counts both sources, by agency. A service date
# runs from 04:00 on its JST day to 04:00 the next, so it can straddle a
# replaced day and a live one; `updates` then says which JST day holds both.
_BOTH_SOURCE_DATES_SQL = (
    "SELECT agency_id, date FROM agg_feed_health "
    "WHERE agency_id = ANY(%s) AND archive_rows > 0 AND live_rows > 0 ORDER BY agency_id, date"
)
_TWO_SOURCE_DAYS_SQL = (
    "SELECT toDate(captured_at, 'Asia/Tokyo') AS day FROM updates "
    "WHERE agency_id = {agency_id:UInt16} AND captured_at >= {lo:DateTime64} AND captured_at < {hi:DateTime64} "
    "AND has({days:Array(Date)}, toDate(captured_at, 'Asia/Tokyo')) "
    f"GROUP BY day HAVING countIf({LIVE_SOURCED}) > 0 AND countIf(NOT {LIVE_SOURCED}) > 0 ORDER BY day"
)


def two_source_days(
    conn, ch_client, agency_ids: Iterable[int], *, now: datetime | None = None
) -> dict[int, list[date]]:
    """Each agency's closed JST days after its retention floor on which
    `updates` holds rows from both the archive and the live path: a
    replacement an ingest left unfinished (pipeline.archive_replace), which
    analyze counts twice. Only the JST days of the service dates whose
    agg_feed_health counts both sources are read, so a day analyze has not
    built since it came to hold both is not found until it has, which is
    also when its aggregates first count it twice. Read-only."""
    ids = sorted(set(agency_ids))
    floors = retention_floors(conn, ids)
    today = jst_date(now or datetime.now(ZoneInfo("UTC")))
    with conn.cursor() as cur:
        cur.execute(_BOTH_SOURCE_DATES_SQL, (ids,))
        dates = cur.fetchall()
    suspects: dict[int, set[date]] = {}
    for agency_id, service_date in dates:
        floor = floors.get(agency_id)
        for day in (service_date, service_date + timedelta(days=1)):
            if day < today and (floor is None or day > floor):
                suspects.setdefault(agency_id, set()).add(day)
    found: dict[int, list[date]] = {}
    for agency_id, days in sorted(suspects.items()):
        params = {
            "agency_id": agency_id,
            "lo": jst_midnight_utc(min(days)),
            "hi": jst_midnight_utc(max(days) + timedelta(days=1)),
            "days": sorted(days),
        }
        rows = ch_client.query(_TWO_SOURCE_DAYS_SQL, parameters=params).result_rows
        if rows:
            found[agency_id] = [row[0] for row in rows]
    return found
