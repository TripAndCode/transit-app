"""Unattended renewal of the RT field-coverage registry.

Every reader of an RT-optional field gates on a live verdict in
``rt_field_coverage_probes`` (``pipeline.strategies.static_join``), and an
expired verdict reads exactly like a missing one, so an agency whose verdict
lapses loses every report built on those fields. This job keeps verdicts
current: it probes each active agency whose ingest strategy can carry the
fields and whose verdict is missing, partial, refuted, expired or within
``RENEW_WITHIN`` of expiring. The decode, the thresholds and the write are
the ones ``scripts/probe_rt_field_coverage.py`` and the admin drawer's probe
button use (``field_coverage``, ``record_field_coverage_probe``), so all
three paths reach the same verdict from the same poll.

It runs unattended, so two guards stand in for the operator who would
otherwise look at the report before recording it:

- Only inside the agency's local service window (``SERVICE_WINDOW_HOURS``).
  This is a cheap pre-filter: an overnight feed is close to empty, and
  fetching it would only produce the skip below.
- A poll with fewer than ``MIN_SAMPLE`` stop_time_updates records nothing.
  A poll of a handful of updates routinely carries arrival estimates on half
  of them, which ``assess_field_coverage`` reads as refuting arr_delay's
  sparse band, and quiet operators send polls that small in the middle of
  the day. This floor is the guard that matters; the window alone would
  still let those through. It applies here rather than inside
  ``assess_field_coverage`` because an operator running the probe by hand
  sees the sample size before deciding to keep the verdict.

Bookkeeping follows ``pipeline.runs``: one ``probe`` row per agency actually
probed (``ok`` when a verdict was written, ``skipped`` when the poll was too
thin or the agency's feed was repointed mid-probe, ``error`` when the fetch
or decode failed), and none at all when nothing is due or the window is
closed, so a poke that has nothing to do draws nothing on the board. One
agency's failure never stops the next one, and nothing here raises to the
caller for a single agency's sake.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline import runs as pipeline_runs
from pipeline.strategies.static_join import (
    RT_COVERAGE_FIELDS,
    RT_INGEST_STRATEGIES,
    field_coverage,
    record_field_coverage_probe,
)
from pipeline.url_guard import safe_urlopen

_log = logging.getLogger(__name__)

#: How far ahead of its expiry a confirmed verdict is renewed. Long enough
#: that an agency whose polls are only sometimes big enough to count gets
#: many daily attempts before its verdict lapses.
RENEW_WITHIN = timedelta(days=14)

#: Local hours [start, end) in which a feed is busy enough to probe.
SERVICE_WINDOW_HOURS = (7, 20)

#: The fewest stop_time_updates a poll needs before its verdict is recorded.
MIN_SAMPLE = 20

#: Per-fetch socket timeout. A feed that slow is not one to measure today;
#: the next run tries again.
FETCH_TIMEOUT_SEC = 15


@dataclass(frozen=True)
class RecordedField:
    """One ``rt_field_coverage_probes`` row, reduced to what decides renewal."""

    field_name: str
    confirmed: bool
    expires_at: datetime | None


@dataclass(frozen=True)
class Candidate:
    """An agency that can carry the RT-optional fields, with its current verdict."""

    agency_id: int
    feed_url: str
    timezone: str
    fields: tuple[RecordedField, ...]


@dataclass
class ReprobeOutcome:
    """Which agencies one run recorded, skipped or failed on."""

    recorded: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def needs_reprobe(fields: Iterable[RecordedField], now: datetime, renew_within: timedelta = RENEW_WITHIN) -> bool:
    """Whether an agency's recorded verdict is due for a fresh probe.

    Due when any field in ``RT_COVERAGE_FIELDS`` has no row, is refuted, or
    expires within *renew_within* of *now*. A refutation is rechecked before
    it expires because it keeps the agency's reports blank for as long as it
    stands. A confirmed row with no expiry is pinned by something more
    durable than one poll and is never due.
    """
    by_name = {f.field_name: f for f in fields}
    for name in RT_COVERAGE_FIELDS:
        row = by_name.get(name)
        if row is None or not row.confirmed:
            return True
        if row.expires_at is not None and row.expires_at <= now + renew_within:
            return True
    return False


def in_service_window(now: datetime, tz_name: str) -> bool:
    """Whether *now*, read on the agency's own clock, falls in its service
    window. An unreadable timezone is never in window: probing at an unknown
    local hour is what the window exists to prevent."""
    try:
        local = now.astimezone(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return False
    start, end = SERVICE_WINDOW_HOURS
    return start <= local.hour < end


def select_due(candidates: Iterable[Candidate], now: datetime) -> list[Candidate]:
    """The candidates to probe now: a due verdict, inside the window."""
    return [c for c in candidates if needs_reprobe(c.fields, now) and in_service_window(now, c.timezone)]


def group_candidates(rows: Iterable[Sequence]) -> list[Candidate]:
    """Fold ``_CANDIDATES_SQL`` rows into one candidate per agency, in row
    order. An agency with no verdict comes back from the LEFT JOIN as a
    single row whose probe columns are NULL."""
    heads: dict[int, tuple[str, str]] = {}
    fields: dict[int, list[RecordedField]] = {}
    for agency_id, feed_url, tz_name, field_name, confirmed, expires_at in rows:
        heads.setdefault(agency_id, (feed_url, tz_name))
        recorded = fields.setdefault(agency_id, [])
        if field_name is not None:
            recorded.append(RecordedField(field_name, confirmed, expires_at))
    return [Candidate(aid, url, tz, tuple(fields[aid])) for aid, (url, tz) in heads.items()]


_CANDIDATES_SQL = """
    SELECT a.agency_id, a.feed_url, a.timezone, p.field_name, p.confirmed, p.expires_at
    FROM agencies a
    LEFT JOIN rt_field_coverage_probes p ON p.agency_id = a.agency_id
    WHERE a.deleted_at IS NULL
      AND a.ingest_strategy = ANY(%s)
    ORDER BY a.agency_id, p.field_name
"""


def load_candidates(conn) -> list[Candidate]:
    """Every active agency whose ingest strategy can carry the RT-optional
    fields, with whatever verdict the registry holds for it."""
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES_SQL, (list(RT_INGEST_STRATEGIES),))
        rows = cur.fetchall()
    # End the read's transaction so the run rows below commit on a clean one.
    conn.rollback()
    return group_candidates(rows)


def fetch_feed(url: str) -> bytes:
    """One poll of *url*, through the guard every server-side fetch of an
    operator-supplied URL uses."""
    with safe_urlopen(url, timeout=FETCH_TIMEOUT_SEC) as resp:
        return resp.read()


# Locks the agency row for the length of the write: a concurrent change of
# feed_url waits for this verdict to commit and then drops it with the rest
# of the agency's verdicts, instead of keeping one measured on the old feed.
_CURRENT_FEED_URL_SQL = "SELECT feed_url FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL FOR SHARE"


async def _record(db_url: str, agency_id: int, feed_url: str, cov: dict) -> dict[str, bool] | None:
    """Write *cov* as *agency_id*'s verdict, or return None without writing
    when the agency no longer points at the feed that was measured."""
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():
            if await conn.fetchval(_CURRENT_FEED_URL_SQL, agency_id) != feed_url:
                return None
            return await record_field_coverage_probe(conn, agency_id, cov, feed_url)
    finally:
        await conn.close()


def reprobe_due_agencies(
    conn,
    db_url: str,
    *,
    now: datetime | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> ReprobeOutcome:
    """Probe every due agency once and record what each poll shows.

    *conn* is a psycopg2 connection, used to read the registry and for the
    ``pipeline_runs`` rows; verdicts are written through
    ``record_field_coverage_probe`` on an asyncpg connection to *db_url* of
    their own. Call from synchronous code with no event loop running.
    """
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_feed
    outcome = ReprobeOutcome()
    for cand in select_due(load_candidates(conn), now):
        aid = cand.agency_id
        try:
            with pipeline_runs.record_run(conn, "probe", agency_id=aid) as run:
                cov = field_coverage(fetch(cand.feed_url))
                run.rows = cov["stop_time_updates"]
                if run.rows < MIN_SAMPLE:
                    _log.info("coverage re-probe: agency %s sent %s stop_time_updates; not recorded", aid, run.rows)
                    run.status = "skipped"
                    outcome.skipped.append(aid)
                    continue
                verdicts = asyncio.run(_record(db_url, aid, cand.feed_url, cov))
                if verdicts is None:
                    _log.warning("coverage re-probe: agency %s changed feeds mid-probe; not recorded", aid)
                    run.status = "skipped"
                    outcome.skipped.append(aid)
                    continue
                _log.info("coverage re-probe: agency %s recorded %s", aid, verdicts)
                outcome.recorded.append(aid)
        except Exception:
            _log.exception("coverage re-probe failed for agency %s", aid)
            outcome.failed.append(aid)
    return outcome
