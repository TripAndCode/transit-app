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
are the retention table in docs/ops-monitoring-deploy.md; ``prune_updates``
below enforces its ClickHouse ``updates`` row.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from pipeline import refiled
from pipeline import runs as pipeline_runs
from pipeline.clickhouse import UPDATES_TABLE
from pipeline.flags import get_flag_state
from pipeline.service_day import service_day_start_utc

logger = logging.getLogger(__name__)

QUERY_LOG_RETENTION_DAYS = 90

#: The control board reads pipeline_runs one JST day at a time (see
#: api/admin_runs.py); a run this far back has no viewer left to show it to.
PIPELINE_RUNS_RETENTION_DAYS = 90

#: Matches UPDATES_RETENTION_DAYS, so the audit trail never outlives the
#: operational data it explains changes to.
ADMIN_AUDIT_RETENTION_DAYS = 400


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
    """DELETE text for the `ask_intent_cache` retention prune. A promoted row
    stays: its `rag_chunks` suggestion resolves its tool and args from it."""
    cutoff = f"now() - INTERVAL '{int(days)} days'"
    return f"DELETE FROM ask_intent_cache WHERE last_used_at < {cutoff} AND promoted_at IS NULL"


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


#: Production ClickHouse `updates` keeps about this many days; older ones are
#: rebuildable from R2.
UPDATES_RETENTION_DAYS = 400

UPDATES_PRUNE_FLAG = "updates_prune_enabled"

_MONTH_PARTITION = re.compile(r"(\d{4})(0[1-9]|1[0-2])")


class UpdatesPartition(NamedTuple):
    partition_id: str  # YYYYMM of captured_at in UTC, the table's partition key
    rows: int
    bytes: int
    agency_ids: tuple[int, ...]


class PartitionVerdict(NamedTuple):
    partition: UpdatesPartition
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


def plan_updates_prune(
    partitions: Iterable[UpdatesPartition], *, cutoff: datetime, oldest_aggregated: Mapping[int, date]
) -> list[PartitionVerdict]:
    """A verdict for each partition whose rows are all past *cutoff*.

    One is dropped only when, for every agency with rows in it, it ends
    before the service day preceding that agency's oldest aggregated date
    (*oldest_aggregated*, from agg_feed_health). analyze reads every date of
    that ledger as rebuildable: a ledger date whose `updates` rows are gone
    is stale and its aggregates are purged, and a full rebuild recomputes
    every date from `updates`. The preceding day is held as well because a
    row captured on it can file a stop event under the oldest date. An agency
    with no ledger holds every partition it has rows in, since its first
    analyze reads them all."""
    verdicts = []
    for partition in partitions:
        _, end = partition_span(partition.partition_id)
        if end > cutoff:
            continue
        held_by = None
        for agency_id in sorted(partition.agency_ids):
            oldest = oldest_aggregated.get(agency_id)
            if oldest is None:
                held_by = f"agency {agency_id} has no aggregates yet"
                break
            if end > service_day_start_utc(oldest - timedelta(days=1)):
                held_by = f"agency {agency_id} has aggregates from {oldest.isoformat()}"
                break
        verdicts.append(PartitionVerdict(partition, held_by))
    return verdicts


_PARTS_SQL = (
    "SELECT partition_id, sum(rows), sum(bytes_on_disk) FROM system.parts "
    "WHERE database = currentDatabase() AND table = {table:String} AND active "
    "GROUP BY partition_id ORDER BY partition_id"
)
_PARTITION_AGENCIES_SQL = (
    f"SELECT _partition_id, groupUniqArray(agency_id) FROM {UPDATES_TABLE} "
    "WHERE _partition_id IN {ids:Array(String)} GROUP BY _partition_id"
)


def read_candidates(ch, cutoff: datetime) -> list[UpdatesPartition]:
    """The `updates` partitions whose rows are all past *cutoff*, with the agencies holding rows in each."""
    parts = ch.query(_PARTS_SQL, parameters={"table": UPDATES_TABLE}).result_rows
    past = [(pid, int(rows), int(size)) for pid, rows, size in parts if partition_span(pid)[1] <= cutoff]
    if not past:
        return []
    agencies = dict(ch.query(_PARTITION_AGENCIES_SQL, parameters={"ids": [pid for pid, _, _ in past]}).result_rows)
    return [UpdatesPartition(pid, rows, size, tuple(sorted(agencies.get(pid, ())))) for pid, rows, size in past]


def oldest_aggregated(conn, agency_ids: Iterable[int]) -> dict[int, date]:
    """Each of *agency_ids*' oldest date in agg_feed_health, analyze's per-date ledger."""
    ids = sorted(set(agency_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id, min(date) FROM agg_feed_health WHERE agency_id = ANY(%s) GROUP BY agency_id", (ids,)
        )
        found = dict(cur.fetchall())
    conn.commit()
    return found


def updates_prune_enabled() -> bool:
    """Kill switch for dropping `updates` partitions, off by default: a drop
    deletes rows only a replay from R2 brings back."""
    return get_flag_state(UPDATES_PRUNE_FLAG).value


def prune_updates(
    conn, ch, *, days: int = UPDATES_RETENTION_DAYS, dry_run: bool = False, now: datetime | None = None
) -> PruneResult:
    """Drop the `updates` partitions plan_updates_prune clears, as one
    `retention` run in pipeline_runs.

    A dry run reads both stores and writes neither, whatever the switch says.
    Otherwise nothing is read or written unless updates_prune_enabled. A
    caller that drops holds the ingest/analyze lock, so no analyze reads a
    month while it goes."""
    enabled = updates_prune_enabled()
    if not dry_run and not enabled:
        logger.info("prune_updates: disabled (%s is off); dropping nothing", UPDATES_PRUNE_FLAG)
        return PruneResult(False, [], [])
    cutoff = updates_cutoff(now or datetime.now(timezone.utc), days)
    candidates = read_candidates(ch, cutoff)
    agencies = {agency_id for partition in candidates for agency_id in partition.agency_ids}
    verdicts = plan_updates_prune(candidates, cutoff=cutoff, oldest_aggregated=oldest_aggregated(conn, agencies))
    if dry_run:
        return PruneResult(enabled, verdicts, [])
    droppable = [verdict.partition for verdict in verdicts if verdict.held_by is None]
    dropped: list[str] = []
    with pipeline_runs.record_run(conn, "retention") as run:
        if droppable:
            refiled.rows_rewritten(ch, sorted({a for partition in droppable for a in partition.agency_ids}))
        for partition in droppable:
            partition_span(partition.partition_id)  # the id is interpolated into DDL
            ch.command(f"ALTER TABLE {UPDATES_TABLE} DROP PARTITION ID '{partition.partition_id}'")
            dropped.append(partition.partition_id)
        run.rows = sum(partition.rows for partition in droppable)
        if not droppable:
            run.status = "unchanged"
    return PruneResult(True, verdicts, dropped)
