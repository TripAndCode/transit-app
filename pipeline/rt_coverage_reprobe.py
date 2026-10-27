"""Unattended renewal of the RT field-coverage registry.

Every reader of an RT-optional field gates on a live verdict in
``rt_field_coverage_probes`` (``pipeline.strategies.static_join``), and an
expired verdict reads exactly like a missing one, so an agency whose verdict
lapses loses every report built on those fields. This job keeps verdicts
current: it probes each active agency whose ingest strategy can carry the
fields and whose verdict is missing, partial, expired or within
``RENEW_WITHIN`` of expiring, or refuted by a probe at least
``RECHECK_AFTER`` ago. The decode, the thresholds and the write are
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
probed, and none at all when nothing is due or the window is closed, so a
poke that has nothing to do draws nothing on the board. This is the
canonical list of how a probe row closes:

- ``ok``: a verdict was written;
- ``skipped``: the poll was too thin to count, or the agency's feed was
  repointed mid-probe;
- ``error``: the feed could not be fetched, was not a GTFS-RT feed, or the
  verdict could not be written. The command-line entry point exits 1.

Neither ``skipped`` nor ``error`` writes to the registry, so the verdict
alone would keep the agency due on every poke. An agency whose latest probe
row started less than ``RETRY_AFTER`` ago is therefore passed over, whatever
that row's status: a deployment that pokes all day retries a struggling feed
a few times across the service window instead of on every poke, and a
once-a-day trigger never meets the gap. Where ``pipeline_runs`` predates the
``probe`` kind no such row exists and there is no gap. One agency's
failure never stops the next one, and nothing here raises to the caller for
a single agency's sake.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline import runs as pipeline_runs
from pipeline.strategies._pb import is_feed_message
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

#: How long a refutation stands before it is probed again. Recording one
#: again does not clear it, so without this a deployment that pokes all day
#: would re-fetch the feed on every poke. Just under a day, so a once-a-day
#: trigger whose start drifts by a few minutes still rechecks it every day.
RECHECK_AFTER = timedelta(hours=20)

#: The least time between two attempts at the same agency, whatever the
#: first one found. Short against the service window, so a feed too thin at
#: the window's open is retried later the same day; a gap of most of a day
#: would push every retry back to the next open, the thinnest hour, and an
#: agency thin only early in the day would never get a verdict. Long enough
#: that hourly pokes try a struggling feed only a few times a day.
RETRY_AFTER = timedelta(hours=3)

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
    probed_at: datetime


class NotAFeed(ValueError):
    """The feed URL answered with bytes that are not a GTFS-RT FeedMessage."""


@dataclass(frozen=True)
class Candidate:
    """An agency that can carry the RT-optional fields, with its current
    verdict and when its latest ``probe`` run started (None: it has none)."""

    agency_id: int
    feed_url: str
    timezone: str
    fields: tuple[RecordedField, ...]
    last_attempt: datetime | None = None


@dataclass
class ReprobeOutcome:
    """Which agencies one run recorded, skipped or failed on."""

    recorded: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def needs_reprobe(fields: Iterable[RecordedField], now: datetime, renew_within: timedelta = RENEW_WITHIN) -> bool:
    """Whether an agency's recorded verdict is due for a fresh probe.

    Due when any field in ``RT_COVERAGE_FIELDS`` has no row, expires within
    *renew_within* of *now*, or is refuted by a probe at least
    ``RECHECK_AFTER`` old. A refutation is rechecked daily rather
    than at its expiry because it keeps the agency's reports blank for as
    long as it stands. A confirmed row with no expiry is pinned by something
    more durable than one poll and is never due.
    """
    by_name = {f.field_name: f for f in fields}
    for name in RT_COVERAGE_FIELDS:
        row = by_name.get(name)
        if row is None:
            return True
        if row.expires_at is not None and row.expires_at <= now + renew_within:
            return True
        if not row.confirmed and row.probed_at <= now - RECHECK_AFTER:
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


def recently_attempted(last_attempt: datetime | None, now: datetime) -> bool:
    """Whether the agency's latest probe run started less than
    ``RETRY_AFTER`` before *now*, whatever it found."""
    return last_attempt is not None and last_attempt > now - RETRY_AFTER


def select_due(candidates: Iterable[Candidate], now: datetime) -> list[Candidate]:
    """The candidates to probe now: a due verdict, inside the window, not
    attempted in the last ``RETRY_AFTER``."""
    return [
        c
        for c in candidates
        if needs_reprobe(c.fields, now)
        and in_service_window(now, c.timezone)
        and not recently_attempted(c.last_attempt, now)
    ]


def group_candidates(rows: Iterable[Sequence]) -> list[Candidate]:
    """Fold ``_CANDIDATES_SQL`` rows into one candidate per agency, in row
    order. An agency with no verdict comes back from the LEFT JOIN as a
    single row whose probe columns are NULL."""
    heads: dict[int, tuple[str, str, datetime | None]] = {}
    fields: dict[int, list[RecordedField]] = {}
    for agency_id, feed_url, tz_name, field_name, confirmed, expires_at, probed_at, last_attempt in rows:
        heads.setdefault(agency_id, (feed_url, tz_name, last_attempt))
        recorded = fields.setdefault(agency_id, [])
        if field_name is not None:
            recorded.append(RecordedField(field_name, confirmed, expires_at, probed_at))
    return [Candidate(aid, url, tz, tuple(fields[aid]), last) for aid, (url, tz, last) in heads.items()]


# The latest probe run per agency rides along in the same round trip, read
# through the partial index migration 0082 adds for exactly this lookup.
_CANDIDATES_SQL = """
    SELECT a.agency_id, a.feed_url, a.timezone, p.field_name, p.confirmed, p.expires_at, p.probed_at,
           (SELECT max(r.started_at) FROM pipeline_runs r
            WHERE r.kind = 'probe' AND r.agency_id = a.agency_id) AS last_attempt
    FROM agencies a
    LEFT JOIN rt_field_coverage_probes p ON p.agency_id = a.agency_id
    WHERE a.deleted_at IS NULL
      AND a.ingest_strategy = ANY(%s)
    ORDER BY a.agency_id, p.field_name
"""


def load_candidates(conn) -> list[Candidate]:
    """Every active agency whose ingest strategy can carry the RT-optional
    fields, with whatever verdict the registry holds for it and when it was
    last attempted."""
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


def _measure(raw: bytes) -> dict:
    """``field_coverage`` of a poll that is a GTFS-RT feed, raising
    ``NotAFeed`` for one that is not: an error page or a truncated body
    decodes leniently to zero updates, which would otherwise pass for a
    quiet feed and be skipped instead of failing."""
    if not is_feed_message(raw):
        raise NotAFeed(f"the feed answered with {len(raw)} bytes that are not a GTFS-RT FeedMessage")
    return field_coverage(raw)


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
                cov = _measure(fetch(cand.feed_url))
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
