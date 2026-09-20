"""Per-time-bucket positioned stop delays for one service day (map day-playback).

The map's live layer answers "where are the buses now". This answers "how did
one whole day unfold" — a dense sequence of frames over the 05:00–24:00 service
window, each frame holding the stops that were observed in that bucket with
their mean departure delay and a position.

Two stores are involved and neither can serve the query alone: the delays live
in ClickHouse `updates` (deduped to one observation per trip-stop event by the
shared builder in `pipeline.db`), while which physical stop a trip visit maps
to, and where that stop is, live in Postgres `static_stop_times`/`static_stops`.
So ClickHouse aggregates to (bucket, trip_id, stop_sequence), Postgres resolves
those pairs to positioned stops, and the fold to (bucket, stop) happens here —
the same split `api.routers.map.route_stop_profile` already uses.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from api.range import RangeCtx
from pipeline.cache import async_lru_cache
from pipeline.reports.filters import _ch_rows, _dedup_cte_ch

# The rail spans the service day, not the calendar day: 05:00 is the first
# departure hour any configured agency runs, and 24:00 closes the window.
# A GTFS extended-hour departure (`scheduled_sec >= 24:00:00`, the
# after-midnight continuation of this same service day) and anything before
# 05:00 both fall outside the rail and are excluded rather than folded into an
# edge bucket, which would attribute their delay to an hour they did not run in.
PLAYBACK_START_SEC = 5 * 3600
PLAYBACK_END_SEC = 24 * 3600

# 60 = one frame per hour (19 frames); 15 = the fine grain (76 frames). Both
# divide the window exactly, which is what lets `bucket_count` be integer
# division and every frame cover the same span.
ALLOWED_STEP_MINUTES = (15, 60)

# A stop needs this many observations within a bucket before it is drawn. One
# or two readings in an hour is a coincidence, not a picture of that stop, and
# at playback speed a viewer has no chance to check a sample count.
MIN_BUCKET_SAMPLES = 3

# Ceiling on the points one frame may carry. A frame is repainted every few
# hundred milliseconds, so the payload is a frame-rate budget, not just a
# transfer one. The cap keeps the best-evidenced stops (most observations),
# so a thinned frame degrades toward the stops the data supports most.
MAX_POINTS_PER_FRAME = 400

# Runaway guard on the ClickHouse side. One agency-day of stop events is
# normally far below this; a feed fault that multiplies trip ids would
# otherwise stream an unbounded result into memory.
MAX_STOP_EVENT_ROWS = 400_000

_MIN = Decimal("0.01")


def bucket_count(step_minutes: int) -> int:
    """Number of frames covering the playback window at ``step_minutes``."""
    return (PLAYBACK_END_SEC - PLAYBACK_START_SEC) // (step_minutes * 60)


def bucket_label(index: int, step_minutes: int) -> str:
    """``"HH:MM"`` clock label for the start of frame ``index``."""
    seconds = PLAYBACK_START_SEC + index * step_minutes * 60
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def bucket_of(scheduled_sec: int | None, step_minutes: int) -> int | None:
    """Frame index for a schedule second, or ``None`` when it is off the rail."""
    if scheduled_sec is None:
        return None
    if scheduled_sec < PLAYBACK_START_SEC or scheduled_sec >= PLAYBACK_END_SEC:
        return None
    return (scheduled_sec - PLAYBACK_START_SEC) // (step_minutes * 60)


def build_timeline_ch_sql(ctx: RangeCtx, step_minutes: int) -> tuple[str, dict]:
    """Render the ClickHouse query behind one service day of playback frames.

    Buckets on `scheduled_sec`, not `captured_at`: the rail is a timetable
    clock. A stop event belongs to the hour it was *scheduled* to depart in,
    which is stable across however many times the feed re-published it, and
    `scheduled_sec` (unlike `scheduled_time`) also carries GTFS extended-hour
    departures instead of dropping them silently — see
    `pipeline.db.build_dedup_ch_sql`.

    Grouped to (bucket, trip_id, stop_sequence) rather than to a stop: which
    physical stop a trip visit maps to lives in Postgres, so that fold happens
    in :func:`fold_stop_buckets` once the mapping has been read.

    Returns ``(sql, parameters)``. ``parameters`` excludes ``agency_id``, which
    the caller supplies — the same contract as every other ClickHouse call
    site in this codebase.
    """
    if step_minutes not in ALLOWED_STEP_MINUTES:
        raise ValueError(f"step_minutes must be one of {ALLOWED_STEP_MINUTES}, got {step_minutes!r}")

    cte_sql, params = _dedup_cte_ch(ctx, include_scheduled_sec=True)
    params = {
        **params,
        "tl_start_sec": PLAYBACK_START_SEC,
        "tl_end_sec": PLAYBACK_END_SEC,
        "tl_step_sec": step_minutes * 60,
        "tl_max_rows": MAX_STOP_EVENT_ROWS,
    }
    sql = (
        f"WITH {cte_sql} "
        "SELECT intDiv(scheduled_sec - {tl_start_sec:UInt32}, {tl_step_sec:UInt32}) AS bucket, "
        "trip_id, stop_sequence, sum(dep_delay) AS delay_sum, count() AS samples "
        "FROM deduped "
        "WHERE scheduled_sec IS NOT NULL "
        "AND scheduled_sec >= {tl_start_sec:UInt32} AND scheduled_sec < {tl_end_sec:UInt32} "
        "GROUP BY bucket, trip_id, stop_sequence "
        "LIMIT {tl_max_rows:UInt32}"
    )
    return sql, params


def fold_stop_buckets(
    rows: list[dict],
    stop_by_pair: dict[tuple[str, int], str],
) -> dict[tuple[int, str], list[int]]:
    """Fold per-trip-visit rows into ``(bucket, stop_id) -> [delay_sum, samples]``.

    A trip visit with no `static_stop_times` mapping has no position, so it is
    dropped rather than pooled into a stop it might not belong to. Several
    routes calling at the same stop in the same bucket pool into one point:
    the rail shows a place, not a service.
    """
    folded: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        bucket = row["bucket"]
        if bucket < 0:
            continue
        stop_id = stop_by_pair.get((row["trip_id"], row["stop_sequence"]))
        if stop_id is None:
            continue
        cell = folded[(bucket, stop_id)]
        cell[0] += int(row["delay_sum"])
        cell[1] += int(row["samples"])
    return dict(folded)


def _round2(value: float) -> float:
    """Half-up to 2 dp, matching every other averaged surface in this codebase."""
    return float(Decimal(str(value)).quantize(_MIN, rounding=ROUND_HALF_UP))


def build_frames(
    folded: dict[tuple[int, str], list[int]],
    stop_geo: dict[str, dict[str, Any]],
    step_minutes: int,
) -> list[dict[str, Any]]:
    """Dense, ordered frames for the whole window.

    Dense — every bucket in the window gets a frame, empty ones included — so
    the playhead advances at a constant rate and the scrubber's position maps
    linearly onto the clock. A sparse list would make a quiet hour indis-
    tinguishable from a fast one.

    ``mean_delay_min`` is sample-weighted across the frame's surviving points,
    never a mean of the per-stop means: a stop with a handful of readings would
    otherwise pull the frame's colour as hard as a hub with hundreds.
    """
    by_bucket: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for (bucket, stop_id), (delay_sum, samples) in folded.items():
        if samples < MIN_BUCKET_SAMPLES:
            continue
        geo = stop_geo.get(stop_id)
        if geo is None:
            continue
        by_bucket[bucket].append(
            {
                "stop_id": stop_id,
                "stop_name": geo["stop_name"],
                "lon": geo["lon"],
                "lat": geo["lat"],
                "avg_delay_min": _round2(delay_sum / samples / 60),
                "samples": samples,
                # Kept out of the payload; only the cap reads it.
                "_delay_sum": delay_sum,
            }
        )

    frames: list[dict[str, Any]] = []
    for index in range(bucket_count(step_minutes)):
        points = by_bucket.get(index, [])
        if len(points) > MAX_POINTS_PER_FRAME:
            points.sort(key=lambda p: (-p["samples"], p["stop_id"]))
            points = points[:MAX_POINTS_PER_FRAME]
        points.sort(key=lambda p: p["stop_id"])
        total_delay = sum(p.pop("_delay_sum") for p in points)
        total_samples = sum(p["samples"] for p in points)
        frames.append(
            {
                "t": bucket_label(index, step_minutes),
                "points": points,
                "mean_delay_min": _round2(total_delay / total_samples / 60) if total_samples else None,
                "samples": total_samples,
            }
        )
    return frames


async def _stop_positions(
    conn, agency_id: int, trip_ids: list[str]
) -> tuple[dict[tuple[str, int], str], dict[str, dict[str, Any]]]:
    """``(trip_id, stop_sequence) -> stop_id`` plus each stop's name and position.

    Scoped to the observed trips rather than the whole agency: a day touches a
    fraction of `static_stop_times`, and `idx_sst_trip` serves exactly this
    shape. Stops with no geometry are omitted from both maps, so a visit that
    cannot be drawn never reaches a frame.
    """
    if not trip_ids:
        return {}, {}
    rows = await conn.fetch(
        """
        SELECT sst.trip_id, sst.stop_sequence, sst.stop_id, ss.stop_name,
            ST_X(ss.geom) AS lon, ST_Y(ss.geom) AS lat
        FROM static_stop_times sst
        JOIN static_stops ss
          ON ss.agency_id = $1 AND ss.stop_id = sst.stop_id AND ss.geom IS NOT NULL
        WHERE sst.agency_id = $1 AND sst.trip_id = ANY($2)
        """,
        agency_id,
        trip_ids,
    )
    by_pair = {(r["trip_id"], r["stop_sequence"]): r["stop_id"] for r in rows}
    geo = {
        r["stop_id"]: {
            "stop_name": r["stop_name"],
            "lon": round(float(r["lon"]), 6),
            "lat": round(float(r["lat"]), 6),
        }
        for r in rows
    }
    return by_pair, geo


@async_lru_cache(maxsize=16, ttl_seconds=600)
async def compute_delay_timeline(
    agency_id: int,
    day: date,
    step_minutes: int,
    conn,
    ch,
) -> list[dict[str, Any]]:
    """Playback frames for one agency-day. Cached per (agency, day, step).

    A finished service day never changes, so the TTL only bounds how long a
    still-accumulating day (today) can be served stale.
    """
    ctx = RangeCtx(from_date=day, to_date=day)
    sql, params = build_timeline_ch_sql(ctx, step_minutes)
    result = await ch.query(sql, parameters={**params, "agency_id": agency_id})
    rows = _ch_rows(result)
    if not rows:
        return build_frames({}, {}, step_minutes)

    trip_ids = list({r["trip_id"] for r in rows})
    by_pair, geo = await _stop_positions(conn, agency_id, trip_ids)
    return build_frames(fold_stop_buckets(rows, by_pair), geo, step_minutes)
