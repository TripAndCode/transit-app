"""Retention windows and the SQL text that enforces them.

Every period the privacy policy states -- per-user usage counts and login
history (``PERSONAL_DATA_RETENTION_MONTHS``), sessions past their expiry, the
unlinked Ask questions (the question log and the intent cache's question
text) and the admin action log, including the address and browser an admin's
action leaves on login history -- is pruned daily by the API process
(``api/retention.py`` via ``policy_prune_sql``), so the stated periods hold
without an external scheduler. Most windows can also be pruned by hand with
their `gtfs_pipeline.py` command.

Intervals are embedded as text rather than bound as parameters: asyncpg has
no placeholder for an INTERVAL literal. Each builder applies ``int()`` itself
rather than trusting the annotation, because that coercion is what makes the
interpolation safe.

The operational stores' horizons (R2, ClickHouse ``updates``, the aggregates)
are the retention table in docs/ops-monitoring-deploy.md. ``prune_updates``
below enforces its ClickHouse ``updates`` row, and ``retention_floor`` is
where analyze stops rebuilding the aggregates of the days it dropped.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from pipeline import refiled
from pipeline import runs as pipeline_runs
from pipeline.clickhouse import UPDATES_TABLE
from pipeline.flags import get_flag_state
from pipeline.locks import agency_ingest_lock
from pipeline.service_day import service_date_ch, service_date_of, service_day_start_utc

logger = logging.getLogger(__name__)

QUERY_LOG_RETENTION_DAYS = 90

#: The control board reads pipeline_runs one JST day at a time (see
#: api/admin_runs.py); a run this far back has no viewer left to show it to.
PIPELINE_RUNS_RETENTION_DAYS = 90

#: Production ClickHouse `updates` keeps about this many days once the prune
#: is on (prune_updates). Older days can be replayed from R2, and the
#: aggregates built from them are kept indefinitely.
UPDATES_RETENTION_DAYS = 400

#: The audit trail never outlives the operational data it explains changes to.
ADMIN_AUDIT_RETENTION_DAYS = UPDATES_RETENTION_DAYS


def prune_pipeline_runs_sql(days: int) -> str:
    """DELETE text for the `pipeline_runs` retention prune."""
    return f"DELETE FROM pipeline_runs WHERE started_at < now() - INTERVAL '{int(days)} days'"


def prune_admin_audit_sql(days: int) -> str:
    """DELETE text for the `admin_audit` retention prune."""
    return f"DELETE FROM admin_audit WHERE at < now() - INTERVAL '{int(days)} days'"


def clear_admin_event_client_sql(days: int) -> str:
    """UPDATE text that clears the address and browser on `login_events` rows
    another account recorded. Those are the acting admin's, so they keep the
    admin action log's period while the row itself keeps login history's.
    Rows already cleared are left alone so the daily pass rewrites nothing."""
    return (
        "UPDATE login_events SET ip = NULL, user_agent = NULL"
        " WHERE actor_id IS NOT NULL AND actor_id IS DISTINCT FROM user_id"
        f" AND created_at < now() - INTERVAL '{int(days)} days'"
        " AND (ip IS NOT NULL OR user_agent IS NOT NULL)"
    )


def prune_query_log_sql(days: int) -> str:
    """DELETE text for the `ask_query_log` retention prune."""
    return f"DELETE FROM ask_query_log WHERE created_at < now() - INTERVAL '{int(days)} days'"


def prune_intent_cache_sql(days: int) -> str:
    """DELETE text for the `ask_intent_cache` retention prune. Every row ages
    out on last use, whatever its `promoted_at`: a row's last_question is text a
    reader typed, and no reader of the cache needs it past the window."""
    cutoff = f"now() - INTERVAL '{int(days)} days'"
    return f"DELETE FROM ask_intent_cache WHERE last_used_at < {cutoff}"


PERSONAL_DATA_RETENTION_MONTHS = 25


def personal_data_prune_sql(months: int) -> list[str]:
    """The personal-data DELETE statements, in the order they run. Usage days
    are Asia/Tokyo calendar days, so their cutoff is too."""
    m = int(months)
    return [
        f"DELETE FROM user_activity_daily WHERE day < (now() AT TIME ZONE 'Asia/Tokyo')::date - INTERVAL '{m} months'",
        f"DELETE FROM login_events WHERE created_at < now() - INTERVAL '{m} months'",
        "DELETE FROM sessions WHERE expires_at < now()",
    ]


def policy_prune_sql() -> list[str]:
    """Every statement the privacy policy's stated periods depend on, in the
    order the daily pass runs them."""
    return [
        *personal_data_prune_sql(PERSONAL_DATA_RETENTION_MONTHS),
        prune_query_log_sql(QUERY_LOG_RETENTION_DAYS),
        prune_intent_cache_sql(QUERY_LOG_RETENTION_DAYS),
        prune_admin_audit_sql(ADMIN_AUDIT_RETENTION_DAYS),
        clear_admin_event_client_sql(ADMIN_AUDIT_RETENTION_DAYS),
    ]


UPDATES_PRUNE_FLAG = "updates_prune_enabled"

_MONTH_PARTITION = re.compile(r"(\d{4})(0[1-9]|1[0-2])")

#: A service date's (raw_samples, total_rows): the pair agg_feed_health keeps
#: per date as analyze's ledger, and the same counts as `updates` holds them.
Counts = tuple[int, int | None]


class UpdatesPartition(NamedTuple):
    partition_id: str  # YYYYMM of captured_at in UTC, the table's partition key
    rows: int
    bytes: int
    agency_ids: tuple[int, ...]


class PartitionVerdict(NamedTuple):
    partition: UpdatesPartition
    floor: date  # the retention floor dropping it sets for its agencies (floor_after)
    held_by: str | None  # why the partition stays, None when it is dropped


class PruneResult(NamedTuple):
    enabled: bool
    verdicts: list[PartitionVerdict]
    dropped: list[str]


def partition_span(partition_id: str) -> tuple[datetime, datetime]:
    """The UTC instants an `updates` partition's rows were captured between
    (start inclusive, end exclusive). Anything but a YYYYMM month is refused:
    the id is interpolated into DDL."""
    match = _MONTH_PARTITION.fullmatch(partition_id)
    if match is None:
        raise ValueError(f"not a UTC-month partition of `updates`: {partition_id!r}")
    year, month = int(match[1]), int(match[2])
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end


def updates_cutoff(now: datetime, days: int) -> datetime:
    """The instant before which `updates` rows are past the horizon."""
    if int(days) < 1:
        raise ValueError(f"the `updates` horizon must be at least one day, got {days}")
    return now - timedelta(days=int(days))


def floor_after(dropped_before: datetime) -> date:
    """The retention floor once every `updates` row captured before
    *dropped_before* is gone: the first service date d such that d and the day
    before it are both whole. The day before counts because a stop event filed
    under d can come from rows captured on it (static_archive.FILED_DATE_SQL)."""
    day = service_date_of(dropped_before)
    if service_day_start_utc(day) < dropped_before:
        day += timedelta(days=1)
    return day + timedelta(days=1)


def retention_floors(conn, agency_ids: Iterable[int]) -> dict[int, date]:
    """retention_floor of each of *agency_ids* that has one."""
    ids = sorted(set(agency_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, max(retention_floor) FROM updates_pruned_months, unnest(agency_ids) AS agency_id "
            "WHERE agency_id = ANY(%s) GROUP BY agency_id",
            (ids,),
        )
        return dict(cur.fetchall())


def retention_floor(conn, agency_id: int) -> date | None:
    """The first service date analyze may still purge or rebuild for
    *agency_id*. Every earlier date is final: `updates` no longer holds all of
    its rows, so its aggregates stand as they were last built. None while no
    month of the agency's rows has been dropped, which leaves every date
    rebuildable, exactly as before any prune.

    Read from the drops prune_updates records rather than from its switch:
    turning the switch off stops further drops, and must not let the next
    analyze purge the aggregates of the months already gone."""
    return retention_floors(conn, [agency_id]).get(agency_id)


def plan_updates_prune(
    partitions: Iterable[UpdatesPartition],
    *,
    cutoff: datetime,
    floors: Mapping[int, date],
    ledgers: Mapping[int, Mapping[date, Counts]],
    live: Mapping[int, Mapping[date, Counts]],
) -> list[PartitionVerdict]:
    """A verdict for each partition whose rows are all past *cutoff*.

    Dropping one raises the retention floor of each agency with rows in it to
    floor_after the partition's end, and analyze then treats every date below
    that floor as final. So a partition is held unless, for each such agency:

    - every service date its rows there are captured on is in the agency's
      ledger (*ledgers*, from agg_feed_health). A date without aggregates,
      such as a catch-up backfill older than the horizon, would otherwise be
      left with nothing;
    - every date from the agency's current floor (*floors*) up to the new one
      counts in its ledger what `updates` holds (*live*), so no stale
      aggregate is frozen. Dates below the current floor are final already,
      and once a month of their rows is gone their counts differ by design.

    *live* must hold, per agency, the counts of every date below the newest
    floor its partitions set, from its current floor or its oldest partition,
    whichever is earlier (read_held_counts). A month held for a date makes
    every later month of that agency wait too, since that date lies below
    their floors as well.
    """
    verdicts = []
    for partition in partitions:
        start, end = partition_span(partition.partition_id)
        if end > cutoff:
            continue
        floor = floor_after(end)
        held_by = None
        for agency_id in sorted(partition.agency_ids):
            held_by = _held_by(
                agency_id, start, end, floor, floors.get(agency_id), ledgers.get(agency_id, {}), live.get(agency_id, {})
            )
            if held_by is not None:
                break
        verdicts.append(PartitionVerdict(partition, floor, held_by))
    return verdicts


def _held_by(
    agency_id: int,
    start: datetime,
    end: datetime,
    floor: date,
    current: date | None,
    ledger: Mapping[date, Counts],
    live: Mapping[date, Counts],
) -> str | None:
    lacking = sorted(
        day
        for day in live
        if day not in ledger
        and service_day_start_utc(day) < end
        and service_day_start_utc(day + timedelta(days=1)) > start
    )
    if lacking:
        return (
            f"agency {agency_id} has {len(lacking)} service date(s) without aggregates, from {lacking[0].isoformat()}"
        )
    stale = sorted(
        day
        for day in {*live, *ledger}
        if day < floor and (current is None or day >= current) and live.get(day) != ledger.get(day)
    )
    if stale:
        return (
            f"agency {agency_id}'s aggregates of {len(stale)} date(s), from {stale[0].isoformat()}, "
            "no longer match `updates`; analyze it first"
        )
    return None


_PARTS_SQL = (
    "SELECT partition_id, sum(rows), sum(bytes_on_disk) FROM system.parts "
    "WHERE database = currentDatabase() AND table = {table:String} AND active "
    "GROUP BY partition_id ORDER BY partition_id"
)
_PARTITION_AGENCIES_SQL = (
    f"SELECT _partition_id, groupUniqArray(agency_id) FROM {UPDATES_TABLE} "
    "WHERE _partition_id IN {ids:Array(String)} GROUP BY _partition_id"
)
# analyze's own ledger counts (pipeline.analyze._ledger_counts), over a range
# of the stored instant so the sort key prunes the scan.
_HELD_COUNTS_SQL = (
    f"SELECT {service_date_ch()} AS date, countIf(dep_delay IS NOT NULL), count() FROM {UPDATES_TABLE} "
    "WHERE agency_id = {agency_id:UInt16} AND captured_at < {before:DateTime64} {after}GROUP BY date"
)


def read_candidates(ch, cutoff: datetime) -> list[UpdatesPartition]:
    """The `updates` partitions whose rows are all past *cutoff*, with the agencies holding rows in each."""
    parts = ch.query(_PARTS_SQL, parameters={"table": UPDATES_TABLE}).result_rows
    past = [(pid, int(rows), int(size)) for pid, rows, size in parts if partition_span(pid)[1] <= cutoff]
    if not past:
        return []
    agencies = dict(ch.query(_PARTITION_AGENCIES_SQL, parameters={"ids": [pid for pid, _, _ in past]}).result_rows)
    return [UpdatesPartition(pid, rows, size, tuple(sorted(agencies.get(pid, ())))) for pid, rows, size in past]


def read_ledgers(conn, bounds: Mapping[int, date]) -> dict[int, dict[date, Counts]]:
    """Each agency's agg_feed_health counts of the dates before its bound in *bounds*."""
    ledgers: dict[int, dict[date, Counts]] = {}
    with conn.cursor() as cur:
        for agency_id, before in sorted(bounds.items()):
            cur.execute(
                "SELECT date, raw_samples, total_rows FROM agg_feed_health WHERE agency_id = %s AND date < %s",
                (agency_id, before),
            )
            ledgers[agency_id] = {day: (raw, total) for day, raw, total in cur.fetchall()}
    return ledgers


def read_held_counts(ch, agency_id: int, before: datetime, after: datetime | None) -> dict[date, Counts]:
    """The agency's ledger counts of the rows `updates` holds captured before
    *before*, and from *after* when given."""
    params: dict = {"agency_id": agency_id, "before": before}
    after_sql = ""
    if after is not None:
        after_sql = "AND captured_at >= {after:DateTime64} "
        params["after"] = after
    rows = ch.query(_HELD_COUNTS_SQL.replace("{after}", after_sql), parameters=params).result_rows
    return {day: (int(raw), int(total)) for day, raw, total in rows}


def _verdicts(conn, ch, candidates: list[UpdatesPartition], cutoff: datetime) -> list[PartitionVerdict]:
    """plan_updates_prune over *candidates*, with the counts it needs read from both stores."""
    floors = retention_floors(conn, {agency_id for partition in candidates for agency_id in partition.agency_ids})
    newest: dict[int, date] = {}
    oldest: dict[int, datetime] = {}
    for partition in candidates:
        start, end = partition_span(partition.partition_id)
        for agency_id in partition.agency_ids:
            newest[agency_id] = max(newest.get(agency_id, date.min), floor_after(end))
            oldest[agency_id] = min(oldest.get(agency_id, start), start)
    ledgers = read_ledgers(conn, newest)
    live = {}
    for agency_id, floor in newest.items():
        current = floors.get(agency_id)
        after = None if current is None else min(oldest[agency_id], service_day_start_utc(current))
        live[agency_id] = read_held_counts(ch, agency_id, service_day_start_utc(floor), after)
    return plan_updates_prune(candidates, cutoff=cutoff, floors=floors, ledgers=ledgers, live=live)


def _filed_dates(partition_id: str) -> list[date]:
    """The service dates a stop event of the partition's rows can be filed
    under: those its rows are captured on, and a day either side."""
    start, end = partition_span(partition_id)
    first = service_date_of(start) - timedelta(days=1)
    last = service_date_of(end - timedelta(microseconds=1)) + timedelta(days=1)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _record_drops(conn, run_id: int | None, verdicts: list[PartitionVerdict]) -> None:
    """Record each partition about to go, which raises its agencies'
    retention floor, and commit before anything is dropped: from here on an
    analyze leaves those dates alone, whether or not the drop follows."""
    with conn.cursor() as cur:
        for verdict in verdicts:
            partition = verdict.partition
            cur.execute(
                "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes, run_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    partition.partition_id,
                    list(partition.agency_ids),
                    verdict.floor,
                    partition.rows,
                    partition.bytes,
                    run_id,
                ),
            )
    conn.commit()


def _mark_dropped(conn, run_id: int | None, partition_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE updates_pruned_months SET dropped_at = now() "
            "WHERE partition_id = %s AND run_id IS NOT DISTINCT FROM %s AND dropped_at IS NULL",
            (partition_id, run_id),
        )
    conn.commit()


def updates_prune_enabled() -> bool:
    """Kill switch for dropping `updates` partitions, off by default: a drop
    deletes rows only a replay from R2 brings back."""
    return get_flag_state(UPDATES_PRUNE_FLAG).value


def prune_updates(
    conn,
    ch,
    *,
    acquire: Callable[[], None],
    days: int = UPDATES_RETENTION_DAYS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PruneResult:
    """Drop the `updates` partitions plan_updates_prune clears, as one
    `retention` run in pipeline_runs.

    The switch is read once, here. A dry run reads both stores and writes
    neither, whatever it says; otherwise nothing is read or written unless it
    is on. A run that may drop calls *acquire*, which takes the ingest/analyze
    lock or ends the process, and then takes each affected agency's `updates`
    lock, ascending as the restamp takes them. analyze reads its floor under
    that lock, so it sees the floor before a drop with every row of it, or
    the floor after.

    Every drop is recorded first (_record_drops), then each partition in turn
    has its refiled dates written off, goes, and is stamped dropped, so a
    failure part-way leaves the months it reached on record and their rows on
    the run.
    """
    enabled = updates_prune_enabled()
    if not dry_run and not enabled:
        logger.info("prune_updates: disabled (%s is off); dropping nothing", UPDATES_PRUNE_FLAG)
        return PruneResult(False, [], [])
    if not dry_run:
        acquire()
    cutoff = updates_cutoff(now or datetime.now(timezone.utc), days)
    candidates = read_candidates(ch, cutoff)
    with ExitStack() as held:
        if not dry_run:
            for agency_id in sorted({a for partition in candidates for a in partition.agency_ids}):
                held.enter_context(agency_ingest_lock(conn, agency_id))
        verdicts = _verdicts(conn, ch, candidates, cutoff)
        if dry_run:
            return PruneResult(enabled, verdicts, [])
        return PruneResult(True, verdicts, _drop(conn, ch, [v for v in verdicts if v.held_by is None]))


def _drop(conn, ch, droppable: list[PartitionVerdict]) -> list[str]:
    dropped: list[str] = []
    with pipeline_runs.record_run(conn, "retention") as run:
        run.rows = 0
        if not droppable:
            run.status = "unchanged"
            return dropped
        _record_drops(conn, run.run_id, droppable)
        for verdict in droppable:
            partition = verdict.partition
            partition_span(partition.partition_id)  # the id is interpolated into DDL
            refiled.rows_rewritten(ch, partition.agency_ids, dates=_filed_dates(partition.partition_id))
            ch.command(f"ALTER TABLE {UPDATES_TABLE} DROP PARTITION ID '{partition.partition_id}'")
            _mark_dropped(conn, run.run_id, partition.partition_id)
            run.rows += partition.rows
            dropped.append(partition.partition_id)
    return dropped
