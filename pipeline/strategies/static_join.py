"""Hiroshima-style RT ingest strategy.

The trip_id in these feeds is an opaque UUID; route_code, service_type, and
scheduled_time are derived by JOINing to static_trips and static_stop_times
on (agency_id, trip_id, stop_sequence).

Rows where the JOIN misses get NULLs in service_type / scheduled_time;
route_code is taken straight from the RT trip.route_id and is always non-null.

An agency's RT-sourced optional fields (``RT_COVERAGE_FIELDS``: stop_id,
arr_delay, schedule_relationship_trip, schedule_relationship_stop) are
trusted only once that agency's OWN live feed has been probed and found to
populate them, as recorded in the ``rt_field_coverage_probes`` registry.
``ingest_strategy == 'static_join'`` alone means a feed's wire shape
matches an already-verified agency's; it does NOT mean the feed populates
these optional fields the same way (see ``field_coverage``'s docstring).
Every reader that trusts these fields calls ``rt_field_coverage_confirmed``
below rather than re-deriving the check inline, so a newly added reader
can't silently skip half the gate.

Verifying a feed is an operational act, not a code change: run
``scripts/probe_rt_field_coverage.py --url <realtime_url> --agency-id <id>
--record`` against that agency's own live feed during active service hours.
An empty/overnight capture (no stop_time_updates at all) confirms nothing
and is refused rather than recorded, and a recorded verdict carries an
expiry so trust in a feed that later changes shape lapses on its own.
"""

import logging

from psycopg2 import sql

from pipeline.strategies._pb import _dec, _fields, decode_feed_timestamp
from pipeline.strategies._time import parse_departure_time

_log = logging.getLogger(__name__)

# Ingest strategies that CAN ever populate stop_id/arr_delay/
# schedule_relationship_*/feed_timestamp -- necessary but not sufficient
# trust; the per-agency probe registry below is the other half of the gate.
# Shared (via this module, and via rt_field_coverage_confirmed below) by
# every reader that needs this check so they can't drift apart if a second
# ingest strategy is ever confirmed.
RT_INGEST_STRATEGIES = frozenset({"static_join"})

# The RT-optional per-stop_time_update fields whose population is verified
# per agency. An agency counts as confirmed only with a live, affirmative
# verdict for EVERY one of them: the readers behind this gate (service
# delivered, dwell/running time, schedule-realism padding, headway quality)
# collectively need all four, and a partial verdict means the feed was never
# fully vetted, not that the unrecorded fields are fine.
RT_COVERAGE_FIELDS = (
    "stop_id",
    "arr_delay",
    "schedule_relationship_trip",
    "schedule_relationship_stop",
)

# How long a recorded probe verdict is trusted before it must be re-run. A
# feed's field population is a property of a vendor deployment that can
# change without notice, so a verdict decays instead of standing forever;
# the horizon is long enough that re-probing stays a periodic operational
# chore rather than a continuous one.
DEFAULT_PROBE_TTL_DAYS = 180

# Coverage fractions separating "this feed populates the field" from "this
# feed happened to emit it once". stop_id and both schedule_relationship_*
# fields are near-universal on a feed that sends them at all; arr_delay is
# sparse by construction (only a StopTimeUpdate carrying an `arrival`
# submessage has one), so it is confirmed by falling strictly INSIDE a band
# -- 0.0 means never sent, and a fraction near 1.0 means the field is not
# the sparse arrival estimate this codebase reads it as.
_NEAR_UNIVERSAL_MIN = 0.99
_ARR_DELAY_SPARSE_RANGE = (0.0, 0.5)  # exclusive lower, exclusive upper

# An agency is confirmed when its ingest strategy CAN send these fields and
# the registry holds a live (unexpired), affirmative verdict for every field
# in RT_COVERAGE_FIELDS. An expired or absent row reads exactly like a
# refuted one here: "never verified" and "verified absent" are both "don't
# trust this field", and only a fresh probe distinguishes them.
_CONFIRMED_AGENCIES_SQL = """
    SELECT p.agency_id
    FROM rt_field_coverage_probes p
    JOIN agencies a ON a.agency_id = p.agency_id
    WHERE a.ingest_strategy = ANY($1::text[])
      AND p.agency_id = ANY($2::int[])
      AND p.field_name = ANY($3::text[])
      AND p.confirmed
      AND (p.expires_at IS NULL OR p.expires_at > now())
    GROUP BY p.agency_id
    HAVING count(DISTINCT p.field_name) = $4::int
"""

_RECORD_PROBE_SQL = """
    INSERT INTO rt_field_coverage_probes
        (agency_id, field_name, confirmed, coverage, sample_size,
         source_feed, probed_at, expires_at)
    VALUES ($1, $2, $3, $4, $5, $6, now(), now() + ($7::int * INTERVAL '1 day'))
    ON CONFLICT (agency_id, field_name) DO UPDATE SET
        confirmed   = EXCLUDED.confirmed,
        coverage    = EXCLUDED.coverage,
        sample_size = EXCLUDED.sample_size,
        source_feed = EXCLUDED.source_feed,
        probed_at   = EXCLUDED.probed_at,
        expires_at  = EXCLUDED.expires_at
"""


async def rt_field_coverage_confirmed_agencies(conn, agency_ids) -> set[int]:
    """The subset of *agency_ids* holding a live, complete RT field-coverage
    verdict, resolved in one round trip.

    Batch form of ``rt_field_coverage_confirmed`` for a reader that gates a
    whole list of agencies at once; both run the same registry query, so a
    per-agency and a batch caller can never disagree about one agency.
    """
    ids = list(agency_ids)
    if not ids:
        return set()
    rows = await conn.fetch(
        _CONFIRMED_AGENCIES_SQL,
        list(RT_INGEST_STRATEGIES),
        ids,
        list(RT_COVERAGE_FIELDS),
        len(RT_COVERAGE_FIELDS),
    )
    return {r["agency_id"] for r in rows}


async def rt_field_coverage_confirmed(agency_id: int, conn) -> bool:
    """True only when *agency_id* both uses an ingest strategy that CAN
    populate the RT-optional fields (``RT_INGEST_STRATEGIES``) AND has a
    live, complete verdict in the ``rt_field_coverage_probes`` registry --
    an ``ingest_strategy`` match alone means a feed's wire shape merely
    matches a confirmed agency's, not that this agency's own live feed has
    been probed and found to actually populate these fields.

    The single canonical home for this per-agency check -- every reader that
    needs it (e.g. dwell time/running time, schedule-realism padding, headway
    quality) imports and calls this directly rather than re-implementing it,
    so a newly added reader can't accidentally trust ``ingest_strategy``
    alone.
    """
    return agency_id in await rt_field_coverage_confirmed_agencies(conn, [agency_id])


def assess_field_coverage(cov: dict) -> dict[str, bool] | None:
    """Turn a ``field_coverage`` result into a per-field confirm/refute
    verdict keyed by ``RT_COVERAGE_FIELDS`` name.

    Returns ``None`` when there is nothing to assess: a poll with zero
    stop_time_updates neither confirms nor refutes anything about a feed's
    field-population habits, and must not be read as a refutation.

    Lives next to the decoder (rather than in the probe CLI) so the exact
    thresholds deciding what gets written to ``rt_field_coverage_probes``
    are defined once, alongside the gate that reads it.
    """
    if cov["stop_time_updates"] == 0:
        return None
    lo, hi = _ARR_DELAY_SPARSE_RANGE
    return {
        "stop_id": cov["stop_id_coverage"] >= _NEAR_UNIVERSAL_MIN,
        "arr_delay": lo < cov["arr_delay_coverage"] < hi,
        "schedule_relationship_trip": cov["schedule_relationship_trip_coverage"] >= _NEAR_UNIVERSAL_MIN,
        "schedule_relationship_stop": cov["schedule_relationship_stop_coverage"] >= _NEAR_UNIVERSAL_MIN,
    }


async def record_field_coverage_probe(
    conn,
    agency_id: int,
    cov: dict,
    source_feed: str,
    ttl_days: int | None = DEFAULT_PROBE_TTL_DAYS,
) -> dict[str, bool]:
    """Persist one probe run's per-field verdicts for *agency_id*, and
    return them.

    *cov* is a ``field_coverage`` result and *source_feed* records what was
    actually probed (the live feed URL, or the path of a capture taken from
    it). ``ttl_days=None`` records a non-expiring verdict, for a feed whose
    coverage is pinned by something more durable than one poll (e.g. a
    checked-in capture the test suite asserts against).

    Raises ``ValueError`` for a capture with no stop_time_updates: an empty
    poll is not evidence, and writing ``confirmed = false`` from one would
    turn "probed at the wrong time of day" into a durable refutation. All
    fields are written in one transaction, so no reader can observe a
    half-updated verdict for an agency.
    """
    verdicts = assess_field_coverage(cov)
    if verdicts is None:
        raise ValueError(
            f"agency {agency_id}: capture has no stop_time_updates -- "
            "an empty poll confirms nothing; re-probe during active service hours"
        )
    async with conn.transaction():
        await conn.executemany(
            _RECORD_PROBE_SQL,
            [
                (
                    agency_id,
                    field,
                    verdicts[field],
                    cov[f"{field}_coverage"],
                    cov["stop_time_updates"],
                    source_feed,
                    ttl_days,
                )
                for field in RT_COVERAGE_FIELDS
            ],
        )
    return verdicts


def _decode_rows(pb_bytes: bytes):
    """Yield (trip_id, rt_route_id, stop_sequence, dep_delay, stop_id, arr_delay,
    schedule_relationship_trip, schedule_relationship_stop) per stop_time_update.

    stop_id (StopTimeUpdate field 4), arr_delay (StopTimeUpdate.arrival's delay,
    field 2 -> field 1), schedule_relationship_trip (TripDescriptor field 4),
    and schedule_relationship_stop (StopTimeUpdate field 5) are populated by
    Hiroshima-style feeds (this strategy's agencies); confirmed absent from
    Aomori's feed (see pipeline/strategies/aomori_regex.py), so no fallback
    decoding for it is needed here.
    """
    try:
        top = _fields(pb_bytes)
    except Exception:
        return
    for ent_bytes in top.get(2, []):
        ent = _fields(ent_bytes)
        if 3 not in ent:
            continue
        tu = _fields(ent[3][0])
        trip_id = rt_route_id = sched_rel_trip = None
        if 1 in tu:
            trip = _fields(tu[1][0])
            if 1 in trip:
                trip_id = _dec(trip[1][0])
            if 5 in trip:
                rt_route_id = _dec(trip[5][0])
            if 4 in trip:
                sched_rel_trip = trip[4][0]
        if not trip_id:
            continue
        for stu_bytes in tu.get(2, []):
            stu = _fields(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            stop_id = _dec(stu[4][0]) if 4 in stu else None
            arr_delay = None
            if 2 in stu:
                arr = _fields(stu[2][0])
                arr_delay = arr.get(1, [None])[0]
            dep_delay = None
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
            sched_rel_stop = stu.get(5, [None])[0]
            yield (trip_id, rt_route_id, stop_seq, dep_delay, stop_id, arr_delay, sched_rel_trip, sched_rel_stop)


def field_coverage(pb_bytes: bytes) -> dict:
    """Report what fraction of stop_time_updates in a feed populate each
    optional field, independent of any static-schedule JOIN or DB state.

    Decodes a raw GTFS-RT FeedMessage exactly as ``parse_feed`` does, but
    with no static-schedule JOIN (no DB connection needed) -- the JOIN only
    matters for ``service_type``/``scheduled_time``, not for whether the RT
    feed itself sends stop_id/arr_delay/schedule_relationship_*/feed_timestamp.
    That makes this usable to check a feed BEFORE an agency row for it even
    exists, which is the point: every reader that trusts an RT-optional
    field gates "is this optional field populated" on a live verdict in the
    ``rt_field_coverage_probes`` registry (this module, via
    ``rt_field_coverage_confirmed``), not on ``ingest_strategy ==
    'static_join'`` alone -- sharing this strategy's opaque-trip_id JOIN
    mechanism only means a feed's wire shape matches an already-verified
    agency's (see ``tests/pipeline/test_static_join.py::
    test_static_join_per_op``'s real-fixture coverage assertions), NOT that
    every feed needing the same JOIN also populates these fields the same
    way. Run this (see ``scripts/probe_rt_field_coverage.py``) against a new
    agency's live feed and record the verdict before any report trusts it.

    Returns ``{"stop_time_updates": int, "feed_timestamp": int | None}``
    plus, only when ``stop_time_updates > 0`` (an empty poll says nothing
    about a feed's field-population habits, good or bad),
    ``{"stop_id_coverage", "arr_delay_coverage",
    "schedule_relationship_trip_coverage",
    "schedule_relationship_stop_coverage"}`` as fractions in ``[0.0, 1.0]``
    of stop_time_updates carrying that field non-NULL.
    """
    raw_rows = list(_decode_rows(pb_bytes))
    result: dict = {
        "stop_time_updates": len(raw_rows),
        "feed_timestamp": decode_feed_timestamp(pb_bytes),
    }
    if not raw_rows:
        return result
    n = len(raw_rows)
    result["stop_id_coverage"] = sum(1 for r in raw_rows if r[4] is not None) / n
    result["arr_delay_coverage"] = sum(1 for r in raw_rows if r[5] is not None) / n
    result["schedule_relationship_trip_coverage"] = sum(1 for r in raw_rows if r[6] is not None) / n
    result["schedule_relationship_stop_coverage"] = sum(1 for r in raw_rows if r[7] is not None) / n
    return result


def parse_feed(
    pb_bytes: bytes,
    captured_at: str,
    file_name: str,
    agency_id: int,
    conn,
) -> list:
    """Return rows shaped for pipeline.clickhouse.insert_updates.

    Row shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
                route_code, stop_sequence, dep_delay, stop_id, arr_delay,
                schedule_relationship_trip, schedule_relationship_stop,
                feed_timestamp, scheduled_sec, static_version_id).
    """
    raw_rows = list(_decode_rows(pb_bytes))
    if not raw_rows:
        return []

    feed_timestamp = decode_feed_timestamp(pb_bytes)

    keys = list({(r[0], r[2]) for r in raw_rows if r[2] is not None})
    if not keys:
        return []

    trip_ids = [k[0] for k in keys]
    stop_seqs = [k[1] for k in keys]

    # Table name is scoped per agency_id, not a single shared name: both
    # cmd_ingest_live's all-agencies branch (gtfs_pipeline.py) and the
    # production cron path (api/routers/internal.py) loop
    # `ingest_live(aid, conn, ...)` over every active agency on ONE shared
    # psycopg2 connection. A single shared table name would make
    # `CREATE TABLE IF NOT EXISTS ... AS SELECT` a no-op for every agency
    # after the first on that connection, silently reusing agency A's
    # schedule rows for agency B's per-file join.
    schedule_table = sql.Identifier(f"_sj_schedule_{int(agency_id)}")
    schedule_idx = sql.Identifier(f"_sj_schedule_{int(agency_id)}_idx")

    with conn.cursor() as cur:
        # No-op after the first call for this agency on this connection: temp
        # tables persist for the whole session (default ON COMMIT PRESERVE
        # ROWS), not just one transaction, and `CREATE TABLE IF NOT EXISTS
        # ... AS SELECT` only runs the SELECT the first time the table
        # doesn't yet exist. This intentionally does NOT pick up a static
        # schedule change made mid-run for a given agency (accepted
        # trade-off; static GTFS data doesn't change during a single ingest
        # run in practice).
        cur.execute(
            sql.SQL(
                "CREATE TEMP TABLE IF NOT EXISTS {} AS "
                "SELECT t.trip_id, st.stop_sequence, t.service_id, st.departure_time, t.static_version_id "
                "FROM static_stop_times st "
                "JOIN static_trips t ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id "
                "WHERE st.agency_id = %s"
            ).format(schedule_table),
            (agency_id,),
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (trip_id, stop_sequence)").format(schedule_idx, schedule_table)
        )
        cur.execute(
            sql.SQL(
                "SELECT s.trip_id, s.stop_sequence, s.service_id, s.departure_time, s.static_version_id "
                "FROM {} s "
                "JOIN unnest(%s::text[], %s::int[]) AS k(trip_id, stop_sequence) "
                "  ON k.trip_id = s.trip_id AND k.stop_sequence = s.stop_sequence"
            ).format(schedule_table),
            (trip_ids, stop_seqs),
        )
        joined = {(tid, seq): (svc, dep, ver) for (tid, seq, svc, dep, ver) in cur.fetchall()}

    rows = []
    miss = 0
    extended = 0
    bad_sched = 0
    for trip_id, rt_route_id, stop_seq, dep_delay, stop_id, arr_delay, sched_rel_trip, sched_rel_stop in raw_rows:
        svc, sched, static_version_id = joined.get((trip_id, stop_seq), (None, None, None))
        if svc is None and sched is None:
            miss += 1
        # Single parse drives the extended-hour handling, the zero-pad, AND
        # the raw scheduled_sec value (see pipeline/strategies/_time.py's
        # module docstring for why: two independent parses of the same
        # field -- this used to check sched[:2] for the >=24 decision and
        # hour_str.isdigit() for padding separately -- can disagree on
        # malformed multi-digit input, e.g. a 3-digit hour like "125:30:00"
        # had a fine 2-char prefix but a broken zero-pad, storing an
        # uncastable value). GTFS's departure_time is raw, unpadded,
        # unvalidated text -- "7:05:00" is as valid as "07:05:00" per spec,
        # and nothing guarantees the rest is even numeric. Postgres's old
        # TIME column validated and normalized this for free; ClickHouse's
        # plain String does not, so analyze()'s `_analyze_deduped.
        # scheduled_time time` column is now the only place format errors
        # surface -- and once a bad value is durably stored in ClickHouse,
        # it fails analyze() for this ENTIRE agency on every subsequent
        # run, not just drops one row. NULL is already a first-class value
        # on this column (every downstream read site handles it -- WHERE
        # scheduled_time IS NOT NULL / toUInt8OrNull).
        sched, status, scheduled_sec = parse_departure_time(sched)
        if status == "extended":
            # GTFS allows departure_time like "25:30:00" for trips spanning
            # midnight as continuation of the previous service day.
            # scheduled_time (Nullable(String), read everywhere as a
            # same-day HH:MM[:SS]) still can't represent it, so that column
            # stays NULL here -- but scheduled_sec (Nullable(Int32)) holds
            # the raw seconds-since-service-day-start value (e.g. 91800 for
            # "25:30:00"), so the row is kept instead of dropped.
            extended += 1
        if status == "bad":
            bad_sched += 1
        rows.append(
            (
                file_name,
                captured_at,
                trip_id,
                svc,
                sched,
                rt_route_id,
                stop_seq,
                dep_delay,
                stop_id,
                arr_delay,
                sched_rel_trip,
                sched_rel_stop,
                feed_timestamp,
                scheduled_sec,
                static_version_id,
            )
        )

    if miss:
        _log.info(f"[static_join] agency={agency_id} {miss}/{len(rows)} rows missed JOIN (logged)")
    if extended:
        _log.info(
            f"[static_join] agency={agency_id} {extended} rows had extended-hour (>=24) departure_time "
            "(kept, scheduled_sec set)"
        )
    if bad_sched:
        _log.warning(f"[static_join] agency={agency_id} {bad_sched} rows had a non-numeric departure_time hour")
    return rows
