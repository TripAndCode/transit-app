"""Observed daily weather ingest, keyed to each agency's representative station.

Feeds `pipeline.reports.weather`'s rain-vs-dry delay comparison. The source is
JMA's (気象庁) published AMeDAS point observations: per-station, 10-minute
readings, which this module sums/averages into the daily values the metric
compares service days on.

Three properties are load-bearing and deliberate:

* **Observation, not forecast.** Only days the source has already observed and
  published are ever written. Nothing here reads a forecast product, and the
  metric built on top must never be presented as one.
* **A day is written whole or not at all.** `aggregate_daily` requires a usable
  reading for every 10-minute slot of the day (see `_READINGS_PER_DAY`) before
  it returns anything. Precipitation is a sum, so a partially-published day
  would understate rainfall and mis-file a wet day as dry -- worse than having
  no row. A day still in progress, or one the source hasn't finished
  publishing, is therefore simply absent and picked up by a later pass; that
  is what tolerating the source's availability lag means here.
* **Revisions are expected, but only inside the publication window.** The
  source revises recently published observations, so `needs_fetch` re-fetches
  an already-stored day while it is still inside `PUBLICATION_WINDOW_DAYS` and
  the upsert overwrites in place, refreshing `retrieved_at`. Past that window
  nothing is requested at all -- neither a revision of a stored day nor a
  first copy of a day that was never stored, because the source no longer
  publishes it and the request could only 404.

Attribution (`attribution`) states both the source and the fact that the daily
figures are computed here from its sub-hourly readings; it must travel with any
figure derived from these rows.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pipeline.url_guard import FeedURLError, safe_urlopen

logger = logging.getLogger(__name__)

# The whole pipeline buckets service days on the JST civil calendar (see
# agg_route_daily's migration), and the source publishes its observations in
# JST as well, so "yesterday" here must mean the JST civil day the aggregates
# are keyed on.
_JST = ZoneInfo("Asia/Tokyo")

# Value of `weather_daily_observations.source` / `agency_weather_stations.source`
# for stations read from JMA's AMeDAS point data. The column exists so a second
# observation source can be added later without re-keying existing rows; every
# row this module writes carries this one value.
WEATHER_SOURCE = "jma_amedas"

# Source AND processing attribution, as the public-data terms require: the
# daily totals/averages are this application's own aggregation of the source's
# 10-minute readings, not figures the source itself published.
_ATTRIBUTION: dict[str, str] = {
    "ja": "出典：気象庁（アメダス観測値）。日別の降水量・気温は本アプリが10分値から集計。",
    "en": (
        "Source: Japan Meteorological Agency (AMeDAS observations). Daily rainfall and "
        "temperature are aggregated from its 10-minute readings by this application."
    ),
}


def attribution(locale: str) -> str:
    """Localized source + processing attribution. Unknown locale falls back to ja."""
    return _ATTRIBUTION.get(locale, _ATTRIBUTION["ja"])


_POINT_URL = "https://www.jma.go.jp/bosai/amedas/data/point/{station_id}/{ymd}_{hour:02d}.json"

# The source publishes point observations in 3-hour files: `_00` covers
# 00:00-02:50, `_21` covers 21:00-23:50. A day's own eight files therefore
# cover 00:00-23:50, and the reading that closes the day out (24:00) is the
# NEXT day's `_00` file, first key. Fetching that extra file is what makes the
# daily window match the source's own 00:00-24:00 daily convention instead of
# being shifted by one 10-minute interval.
_BLOCK_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)

# 10-minute readings in a full day, i.e. (00:00, 24:00].
_READINGS_PER_DAY = 144

# Each measurement is published as `[value, quality_flag]`. Flag 0 is a normal
# observation; every other flag marks a value the source does not vouch for as
# one (missing, not observed at this station, provisional). Only flag 0 is
# accepted -- a daily precipitation total is only meaningful when every reading
# that composes it is a normal observation.
_NORMAL_QUALITY_FLAG = 0

# One 3-hour block is tens of KB; this cap is orders of magnitude above that,
# and only there to keep a misbehaving/redirected endpoint from streaming an
# unbounded body into memory (url_guard's own default cap is sized for GTFS
# static zips, far too generous for a small JSON document).
_MAX_BLOCK_BYTES = 8 * 1024 * 1024

# Bound on the SUM of the nine blocks a single station-day merges into one
# dict. A per-block cap alone lets an endpoint serving nine merely-large
# bodies hold nine times that much at once, and this ingest also runs inside
# the long-lived API process (the cron path in api.routers.internal), so the
# peak that matters is the merged total, not any one response. Still orders of
# magnitude above a real day's payload; exceeding it aborts the day rather
# than truncating it, since a partial day is never written anyway.
_MAX_DAY_BYTES = 16 * 1024 * 1024

# Per-socket-operation ceiling, NOT a budget for a run: it bounds one connect
# or one read, so a source trickling its bodies can keep every one of a
# station-day's nine blocks inside its own timeout and still take minutes, and
# a pass multiplies that by station-days and by stations. A caller with a
# deadline (`ingest_weather`'s `max_seconds`) is what keeps a run near its
# budget: it is threaded down to every block fetch, which both refuses to
# start past the deadline and clamps this ceiling to whatever is left of it.
# That still leaves one overrun uncovered -- a body arriving a few bytes at a
# time re-arms the ceiling on every socket operation, so a trickling source can
# hold a single already-started read open past the deadline indefinitely.
_FETCH_TIMEOUT_SEC = 20.0

# How long the source keeps a day's point observations published. This single
# window decides every fetch: inside it a stored day is still re-fetched to
# pick up the source's revisions and a never-stored day is still worth a first
# attempt, while outside it the copy on hand is final and a day that was never
# stored can never be filled -- so asking for it would 404 on every pass,
# forever. It is therefore also the ceiling on how many days back a pass may
# walk, since days beyond it are unfetchable by construction.
PUBLICATION_WINDOW_DAYS = 5

# Wall-clock budget for a weather pass that shares its connection -- and so
# its session-level advisory locks -- with other work. The cron path in
# api.routers.internal holds the ingest+analyze lock for as long as its
# `ingest_weather` call runs, and every cron poke arriving meanwhile is
# dropped, losing live GTFS-RT polls that cannot be recovered after the fact.
# Sized to stay small next to the cron interval rather than to fit a whole
# pass: a station-day the budget cuts short is an ordinary "not ready yet"
# outcome that a later pass picks up.
CRON_INGEST_BUDGET_SEC = 90.0

# Minimum age of a stored copy before it is re-fetched for revisions. Keeps a
# frequently-invoked ingest pass (the cron path pokes far more often than the
# source revises) from re-fetching the same recent days on every run.
_RECHECK_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class DailyObservation:
    """One station's observed values for one whole day.

    `precip_mm` is required: the metric these rows feed is keyed on rainfall,
    so a station that cannot supply a whole observed day of rain readings (a
    temperature-only site, or a day the source hasn't finished publishing)
    yields no observation at all rather than a rainless-looking one. A dry day
    is `0.0`, never `None`. Temperature is optional and independent -- a
    station may report rain without it.
    """

    station_id: str
    obs_date: date
    precip_mm: float
    temp_avg_c: float | None
    temp_max_c: float | None
    temp_min_c: float | None


def _usable(entry: Any) -> float | None:
    """Unwrap a published `[value, quality_flag]` pair, or `None` if unusable."""
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return None
    value, flag = entry
    if flag != _NORMAL_QUALITY_FLAG:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number):
        # NaN/Infinity are rejected here rather than left to the storage
        # layer: Postgres evaluates both `'NaN'::float8 >= 0` and
        # `'Infinity'::float8 >= 0` as true, so the CHECK on precip_mm cannot
        # catch them. One such reading in a day's sum makes the whole daily
        # total non-finite, which stores as a permanently wrong precip_mm,
        # mis-files the day as rainy, and turns the entire wet side's rainfall
        # average into a non-finite value.
        return None
    return number


def _day_window(obs_date: date) -> tuple[str, str]:
    """Half-open `(exclusive_start, inclusive_end)` timestamp keys for *obs_date*.

    The source keys readings as zero-padded ``YYYYMMDDHHMMSS`` in JST, so the
    fixed-width strings compare lexicographically in chronological order and no
    parsing is needed. The window is ``(00:00, 24:00]`` -- a 10-minute reading
    is stamped at the END of the interval it measures, so the 00:00 reading
    belongs to the previous day and the next day's 00:00 reading closes this
    one out.
    """
    return (
        obs_date.strftime("%Y%m%d") + "000000",
        (obs_date + timedelta(days=1)).strftime("%Y%m%d") + "000000",
    )


def aggregate_daily(
    station_id: str,
    obs_date: date,
    readings: Mapping[str, Any],
) -> DailyObservation | None:
    """Aggregate 10-minute point readings into one day. Pure (no I/O).

    *readings* maps ``YYYYMMDDHHMMSS`` (JST) to that reading's published field
    mapping; keys outside *obs_date*'s ``(00:00, 24:00]`` window are ignored,
    so a caller may merge several 3-hour blocks (plus the next day's first
    block) and pass the union.

    Returns ``None`` when the day is incomplete -- fewer than
    `_READINGS_PER_DAY` slots present, or any slot without a normal-quality
    ``precipitation10m`` reading. Rainfall is a sum over the whole day, so a
    partial total is not a smaller-but-valid answer, it is a wrong one that
    reads as a drier day than actually occurred.

    A station that reports rainfall but no usable temperature still yields a
    row, with the temperature fields ``None``: temperature is a mean/extremum,
    not a sum, and the metric this feeds keys on rainfall. A station with no
    usable rain readings at all yields nothing, for the same reason.
    """
    start, end = _day_window(obs_date)

    precip: list[float] = []
    temps: list[float] = []
    for key, fields in readings.items():
        if len(key) != 14 or not key.isdigit():
            continue
        if not (start < key <= end):
            continue
        if not isinstance(fields, Mapping):
            continue
        mm = _usable(fields.get("precipitation10m"))
        if mm is None:
            # An unusable slot inside the window fails the whole day below;
            # counting it here would let a missing reading pass as 0 mm.
            continue
        precip.append(mm)
        temp = _usable(fields.get("temp"))
        if temp is not None:
            temps.append(temp)

    if len(precip) < _READINGS_PER_DAY:
        logger.debug(
            "weather: station %s %s incomplete (%d/%d usable 10-minute readings)",
            station_id,
            obs_date,
            len(precip),
            _READINGS_PER_DAY,
        )
        return None

    return DailyObservation(
        station_id=station_id,
        obs_date=obs_date,
        # 1 dp matches the source's own published resolution; summing ~144
        # floats otherwise leaves binary-representation noise in the total.
        precip_mm=round(sum(precip), 1),
        temp_avg_c=round(sum(temps) / len(temps), 1) if temps else None,
        temp_max_c=max(temps) if temps else None,
        temp_min_c=min(temps) if temps else None,
    )


def _fetch_block(
    station_id: str,
    day: date,
    hour: int,
    max_bytes: int,
    timeout: float = _FETCH_TIMEOUT_SEC,
) -> tuple[dict[str, Any], int] | None:
    """Fetch one published 3-hour block as ``(payload, bytes_read)``, or ``None``.

    *max_bytes* is the caller's remaining budget for this station-day, already
    clamped to `_MAX_BLOCK_BYTES`; a body over it raises inside `safe_urlopen`
    and degrades to ``None`` like any other unreadable block. The byte count
    is returned so the caller can debit its running total.

    *timeout* is the per-socket-operation ceiling, which a caller working
    against a wall-clock deadline passes clamped to the time it has left. That
    bounds a source that stops responding outright, but not one that keeps
    trickling: the ceiling re-arms on every socket operation, so a body
    delivered a few bytes at a time stays inside it indefinitely and can carry
    the call arbitrarily far past the caller's deadline. Bounding that would
    take a deadline on the body read itself, which `safe_urlopen` does not
    currently expose.

    Every failure mode degrades to ``None`` rather than raising: a block that
    isn't published yet (or has aged out of the source's rolling retention) is
    an ordinary, expected outcome for an ingest pass, and the caller turns a
    missing block into "this day isn't ready" rather than an error.
    """
    # station_id is percent-encoded because it comes from a hand-populated table
    # and lands in the URL's path: an unencoded `/`, `?` or `#` in it would point
    # the fetch at a different document on the host than this template names.
    url = _POINT_URL.format(station_id=quote(station_id, safe=""), ymd=day.strftime("%Y%m%d"), hour=hour)
    try:
        with safe_urlopen(url, timeout=timeout, max_bytes=max_bytes) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug("weather: no published block for station %s %s hour %02d", station_id, day, hour)
        else:
            logger.warning("weather: HTTP %s fetching station %s %s hour %02d", e.code, station_id, day, hour)
        return None
    except (FeedURLError, OSError, ValueError, UnicodeDecodeError) as e:
        # ValueError covers json.JSONDecodeError; OSError covers URLError and
        # socket timeouts.
        logger.warning("weather: failed to read station %s %s hour %02d: %s", station_id, day, hour, e)
        return None
    if not isinstance(payload, dict):
        logger.warning("weather: unexpected payload shape for station %s %s hour %02d", station_id, day, hour)
        return None
    return payload, len(raw)


def fetch_daily_observation(
    station_id: str,
    obs_date: date,
    *,
    deadline: float | None = None,
) -> DailyObservation | None:
    """Fetch and aggregate one station-day, or ``None`` if it isn't fully available.

    Reads *obs_date*'s own eight 3-hour blocks plus the next day's first block
    (see `_BLOCK_HOURS`). A missing block short-circuits to ``None`` instead of
    aggregating what did arrive -- the day is either wholly available or left
    for a later pass.

    The blocks are merged into one dict, so `_MAX_DAY_BYTES` bounds their
    combined size: each block is fetched with whatever is left of that budget
    (never more than `_MAX_BLOCK_BYTES`), and a station-day that would exceed
    it is abandoned rather than accumulated.

    *deadline* is an optional `time.monotonic` instant this call must not run
    past. It is checked before every block and clamps each block's socket
    timeout to the time remaining, because nine blocks each entitled to a full
    `_FETCH_TIMEOUT_SEC` would otherwise let one station-day overrun its
    caller's whole budget several times over. Expiry aborts the day exactly
    like a block the source has not published: ``None``, nothing stored, the
    day left for a later pass -- never a partially aggregated day, whose
    rainfall sum would read as drier than the day actually was. Omitting it
    leaves the call unbounded, which is what a standalone CLI run wants.
    """
    blocks: list[tuple[date, int]] = [(obs_date, h) for h in _BLOCK_HOURS]
    blocks.append((obs_date + timedelta(days=1), 0))

    readings: dict[str, Any] = {}
    remaining = _MAX_DAY_BYTES
    timeout = _FETCH_TIMEOUT_SEC
    for day, hour in blocks:
        if remaining <= 0:
            logger.warning(
                "weather: station %s %s exceeded the %d-byte per-day fetch budget; skipping the day",
                station_id,
                obs_date,
                _MAX_DAY_BYTES,
            )
            return None
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                logger.debug(
                    "weather: station %s %s ran out of time mid-day; leaving it for a later pass",
                    station_id,
                    obs_date,
                )
                return None
            timeout = min(_FETCH_TIMEOUT_SEC, left)
        fetched = _fetch_block(station_id, day, hour, min(_MAX_BLOCK_BYTES, remaining), timeout)
        if fetched is None:
            return None
        payload, n_bytes = fetched
        remaining -= n_bytes
        readings.update(payload)
    return aggregate_daily(station_id, obs_date, readings)


def needs_fetch(
    obs_date: date,
    stored_retrieved_at: datetime | None,
    *,
    today: date,
    now: datetime,
) -> bool:
    """Should this station-day be fetched? Pure.

    Outside `PUBLICATION_WINDOW_DAYS` -> no, whatever is or isn't stored: the
    source has stopped publishing the day, so a stored copy is final and a
    missing one can never be filled. The age floor matters as much as the
    staleness ceiling -- without it a day the source never published while it
    was fetchable would be re-requested by every pass for as long as it stayed
    in range, and 404 every time.

    Inside the window: never stored -> yes; stored -> only once the copy on
    hand is at least `_RECHECK_AFTER` old, so an ingest pass that runs far
    more often than the source revises doesn't re-fetch the same days on every
    run.
    """
    if (today - obs_date).days > PUBLICATION_WINDOW_DAYS:
        return False
    if stored_retrieved_at is None:
        return True
    return now - stored_retrieved_at >= _RECHECK_AFTER


def weather_ingest_enabled() -> bool:
    """Kill switch for every outbound weather fetch, opt-in (default off).

    Gates the ingest pass itself rather than its callers, so the CLI and the
    cron path cannot diverge on whether this application reaches out to the
    source at all. Disabled is a graceful no-op: the report side reads
    "not available" from the absence of rows, which is exactly what it already
    does for an agency with no representative station configured.
    """
    return os.environ.get("WEATHER_INGEST_ENABLED", "false").strip().lower() in ("1", "true", "yes")


_STATIONS_SQL = """
    SELECT DISTINCT s.station_id
    FROM agency_weather_stations s
    JOIN agencies a ON a.agency_id = s.agency_id AND a.deleted_at IS NULL
    WHERE s.source = %s
    ORDER BY s.station_id
"""

_STORED_SQL = """
    SELECT obs_date, retrieved_at
    FROM weather_daily_observations
    WHERE station_id = %s AND obs_date >= %s AND obs_date <= %s
"""

_UPSERT_SQL = """
    INSERT INTO weather_daily_observations
        (station_id, obs_date, precip_mm, temp_avg_c, temp_max_c, temp_min_c, source, retrieved_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (station_id, obs_date) DO UPDATE SET
        precip_mm    = EXCLUDED.precip_mm,
        temp_avg_c   = EXCLUDED.temp_avg_c,
        temp_max_c   = EXCLUDED.temp_max_c,
        temp_min_c   = EXCLUDED.temp_min_c,
        source       = EXCLUDED.source,
        retrieved_at = now()
"""


def ingest_weather(
    conn,
    *,
    days: int = PUBLICATION_WINDOW_DAYS,
    today: date | None = None,
    max_seconds: float | None = None,
) -> tuple[int, int, list[str]]:
    """Fetch observed daily weather for every configured representative station.

    Walks the *days* whole days ending yesterday -- today is never fetched,
    since a day in progress cannot be aggregated whole -- for each distinct
    station referenced by a live agency, fetching only the station-days
    `needs_fetch` asks for. *days* is clamped to `PUBLICATION_WINDOW_DAYS`,
    which is what makes an operator-supplied backfill window harmless: a day
    beyond it is unfetchable, so walking back further would only issue
    requests that must 404.

    Returns ``(n_written, n_considered, failed)``: *n_considered* counts
    station-days examined, *failed* the station_ids that raised, following
    `pipeline.static_fetcher.refresh_all`'s run-all-then-report shape so one
    bad station doesn't starve the others.

    *max_seconds* bounds the whole pass's wall clock, checked before each
    station, before each station-day's fetch, and -- threaded down as a
    deadline -- before each of the nine blocks that fetch reads, so a slow
    source stops the pass mid-day instead of only at the next day or station
    boundary. On expiry the pass stops and returns what it has already
    committed. Any caller sharing this connection, and therefore its
    session-level locks, with other work must pass one (see
    `CRON_INGEST_BUDGET_SEC`): `_FETCH_TIMEOUT_SEC` bounds a single socket
    operation, not a run, so an unbounded pass can hold those locks for far
    longer than any per-request timeout suggests.

    A station-day the source hasn't fully published, or that the budget cut
    short, is not a failure; it is skipped and picked up by a later pass.

    No transaction is ever left open across a fetch. The reads are committed
    before any outbound request and each written station-day is committed on
    its own, because a session idling in a transaction across dozens of
    network calls pins its `xmin` -- keeping autovacuum from reclaiming the
    `agg_*` churn the cron path produces right before this runs -- and holds
    `(station_id, obs_date)` row locks that a concurrent pass would block on.
    Per-day commits give up nothing: each row is an independently valid whole
    day, so a station that raises part-way keeps the days it already
    committed, rolls back only the one in flight, and the loop moves on.
    """
    if not weather_ingest_enabled():
        # Info, not debug: this is the documented behavior of the default-off
        # switch, and the CLI configures logging at info level, so an operator
        # who has not turned it on must be able to tell "skipped, switch off"
        # from "ran, nothing to do".
        logger.info("weather: WEATHER_INGEST_ENABLED is not set; skipping weather ingest")
        return (0, 0, [])
    if days < 1:
        return (0, 0, [])
    days = min(days, PUBLICATION_WINDOW_DAYS)

    today = today or datetime.now(timezone.utc).astimezone(_JST).date()
    to_date = today - timedelta(days=1)
    from_date = to_date - timedelta(days=days - 1)
    now = datetime.now(timezone.utc)
    # Monotonic: a wall-clock jump (NTP step, DST) must not extend or truncate
    # a budget whose whole job is bounding how long a lock is held.
    deadline = None if max_seconds is None else time.monotonic() + max_seconds

    try:
        with conn.cursor() as cur:
            cur.execute(_STATIONS_SQL, (WEATHER_SOURCE,))
            station_ids = [r[0] for r in cur.fetchall()]
        # Ends this read's transaction before the first outbound request.
        conn.commit()
    except Exception:
        # Roll back before re-raising so the caller's session stays usable: the
        # cron path runs an aggregate-freshness check on this same connection
        # afterwards, and an aborted transaction would take that down too.
        conn.rollback()
        raise
    if not station_ids:
        logger.info("weather: no agency has a representative station configured; nothing to ingest")
        return (0, 0, [])

    written = 0
    considered = 0
    failed: list[str] = []
    out_of_budget = False
    for station_id in station_ids:
        if deadline is not None and time.monotonic() >= deadline:
            out_of_budget = True
            break
        try:
            with conn.cursor() as cur:
                cur.execute(_STORED_SQL, (station_id, from_date, to_date))
                stored = {r[0]: r[1] for r in cur.fetchall()}
            conn.commit()
            for offset in range((to_date - from_date).days + 1):
                obs_date = from_date + timedelta(days=offset)
                if not needs_fetch(obs_date, stored.get(obs_date), today=today, now=now):
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    out_of_budget = True
                    break
                considered += 1
                obs = fetch_daily_observation(station_id, obs_date, deadline=deadline)
                if obs is None:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        _UPSERT_SQL,
                        (
                            obs.station_id,
                            obs.obs_date,
                            obs.precip_mm,
                            obs.temp_avg_c,
                            obs.temp_max_c,
                            obs.temp_min_c,
                            WEATHER_SOURCE,
                        ),
                    )
                conn.commit()
                # Counted only once the commit that persisted it succeeded: an
                # operator reading "wrote N" and finding fewer rows than that
                # would have no way to tell the difference.
                written += 1
        except Exception:
            logger.exception("weather: ingest failed for station %s", station_id)
            conn.rollback()
            failed.append(station_id)
        if out_of_budget:
            break

    if out_of_budget:
        logger.warning(
            "weather: ran out of the %s-second budget; the station-days not reached are left for a later pass",
            max_seconds,
        )
    logger.info(
        "weather: wrote %d of %d station-days examined (%s..%s, %d station(s))",
        written,
        considered,
        from_date,
        to_date,
        len(station_ids),
    )
    return (written, considered, failed)
