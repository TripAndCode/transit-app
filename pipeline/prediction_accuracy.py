"""Prediction-accuracy metric from repeated pre-dedup GTFS-RT observations.

A GTFS-RT feed re-broadcasts a refining `dep_delay` estimate for the same
stop event roughly every ~30s as the trip approaches (see
`pipeline.db.build_dedup_ch_sql`'s docstring); `analyze()` and every report
read only the LATEST such observation per stop event (the "final" one) --
what a rider actually experienced. Before that collapse throws every earlier
observation away, this module measures how far off an early observation's
predicted delay was from that final, most-authoritative one -- bucketed by
how much lead time the early observation had (how long, at the moment it
was captured, before the departure IT ITSELF predicted) -- so a caller can
answer "how much should I trust an estimate made 10 minutes before
departure vs. 30 seconds before?".

No new schema: this reads the same `updates` columns
`pipeline.db.build_dedup_ch_sql` already reads (`captured_at`, `dep_delay`,
`scheduled_time`, ...); see `pipeline.db.build_prediction_accuracy_ch_sql`
for the live, non-persisted ClickHouse query this module's aggregation
consumes. A stop event observed only once has no early reading to compare
and contributes nothing.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta
from datetime import time as _time
from zoneinfo import ZoneInfo

from pipeline.histogram import bucketize, n_buckets_for

# Every `updates` timestamp/date this codebase buckets by civil day uses
# Asia/Tokyo (see pipeline.db.build_dedup_ch_sql's own `toDate(..., 'Asia/
# Tokyo')` note) -- the static schedule's `scheduled_time` is a JST
# service-day-local time, so combining it with `date` needs the same zone to
# produce a correct absolute instant.
_JST = ZoneInfo("Asia/Tokyo")

# Lead time = seconds remaining, AT OBSERVATION TIME, until the departure
# THAT observation itself predicted (its own scheduled_time + its own
# dep_delay) -- not until the eventual final departure. Bounds tuned to the
# ~30s repoll cadence and a trip's realistic RT-visibility horizon: most
# stop events are first observed with 0-20 minutes of lead time. A negative
# lead time (the observation landed AFTER its own predicted departure had
# already passed -- possible once a later, larger dep_delay estimate
# supersedes an earlier, smaller one) lands in the underflow bucket like any
# other out-of-range reading, rather than being dropped.
LEAD_LO = 0
LEAD_HI = 1200
LEAD_WIDTH = 120
LEAD_N_BUCKETS = n_buckets_for(lo=LEAD_LO, hi=LEAD_HI, width=LEAD_WIDTH)


def bucketize_lead_time(lead_time_sec: int) -> int:
    """Bucket index for a lead-time reading. See :data:`LEAD_LO`/
    :data:`LEAD_HI`/:data:`LEAD_WIDTH`."""
    return bucketize(lead_time_sec, lo=LEAD_LO, hi=LEAD_HI, width=LEAD_WIDTH)


def _parse_scheduled_time(value: _time | str) -> _time:
    """Coerce a stop event's `scheduled_time` into a `datetime.time`.

    `build_prediction_accuracy_ch_sql` reads `scheduled_time` straight from
    ClickHouse, where it's stored as a bare string -- `"HH:MM"` or
    `"HH:MM:SS"`, depending on the ingest strategy (see
    `pipeline.clickhouse.UPDATE_COLUMNS`'s `scheduled_time` column) -- not a
    typed time value; a caller that already has a real `datetime.time` (a
    test fixture, or a future caller reading Postgres's typed
    `_analyze_deduped.scheduled_time` instead) is passed through unchanged.
    Extended-hour GTFS notation (">=24:00:00") never reaches `updates` at
    all -- see `pipeline.strategies._time.normalize_departure_time`'s
    docstring -- so every string this sees parses as a plain "H:MM[:SS]"
    time-of-day.
    """
    if isinstance(value, _time):
        return value
    hh_str, mm_str, *rest = value.split(":")
    return _time(int(hh_str), int(mm_str), int(rest[0]) if rest else 0)


def scheduled_departure_at(service_date: _date, scheduled_time: _time | str) -> datetime:
    """Absolute scheduled-departure instant for a stop event: the static
    schedule's `scheduled_time` combined with its JST service `date` (the
    same pair `pipeline.db.build_dedup_ch_sql`'s dedup groups by), made
    timezone-aware in Asia/Tokyo so it can be diffed directly against a
    (UTC) `captured_at`. `scheduled_time` may be a `datetime.time` or the
    raw ClickHouse string form -- see :func:`_parse_scheduled_time`.
    """
    return datetime.combine(service_date, _parse_scheduled_time(scheduled_time), tzinfo=_JST)


def prediction_error(
    scheduled_departure: datetime,
    early_captured_at: datetime,
    early_dep_delay: int,
    final_dep_delay: int,
) -> tuple[int, int]:
    """Return `(lead_time_sec, error_sec)` for ONE early observation,
    compared against its stop event's final (most authoritative, latest-
    observed) `dep_delay`.

    `error_sec` is `early_dep_delay - final_dep_delay`: positive means the
    early observation OVERESTIMATED how late the trip would be (predicted a
    later departure than what actually happened), negative means it
    UNDERESTIMATED.
    """
    predicted_departure = scheduled_departure + timedelta(seconds=early_dep_delay)
    lead_time_sec = round((predicted_departure - early_captured_at).total_seconds())
    error_sec = early_dep_delay - final_dep_delay
    return lead_time_sec, error_sec


def compute_stop_event_errors(
    scheduled_departure: datetime,
    observations: list[tuple[datetime, int]],
) -> list[dict]:
    """One dict per EARLY observation in *observations* --
    `{"lead_time_sec": int, "lead_bucket": int, "error_sec": int}` -- for a
    single stop event, excluding whichever observation is the FINAL one (the
    one with the maximal `captured_at`).

    `observations` is `(captured_at, dep_delay)` pairs for one stop event
    (the same route_code/service_type/scheduled_time/trip_id/date/
    stop_sequence group `pipeline.db.build_dedup_ch_sql` groups by), in any
    order. A tie on the maximal `captured_at` resolves to whichever tied
    entry comes first in *observations* -- unlike
    `pipeline.db.build_dedup_ch_sql`'s dedup (which breaks a `captured_at`
    tie via `file_name DESC` because it must pick exactly one canonical
    value every other aggregate reads), this metric is a bucketed
    distribution over many observations, so which of two simultaneous
    file-poll rows counts as "final" does not materially change it -- the
    simpler max-by-`captured_at` rule is used instead of threading
    `file_name` through this pure function too.

    Returns `[]` for a stop event observed only once: there is no early
    reading to compare against a final one.
    """
    if len(observations) < 2:
        return []
    final_index = max(range(len(observations)), key=lambda i: observations[i][0])
    final_dep_delay = observations[final_index][1]
    out = []
    for i, (captured_at, dep_delay) in enumerate(observations):
        if i == final_index:
            continue
        lead_time_sec, error_sec = prediction_error(scheduled_departure, captured_at, dep_delay, final_dep_delay)
        out.append(
            {
                "lead_time_sec": lead_time_sec,
                "lead_bucket": bucketize_lead_time(lead_time_sec),
                "error_sec": error_sec,
            }
        )
    return out


def aggregate_by_lead_bucket(observation_errors: list[dict]) -> list[dict]:
    """Merge many `compute_stop_event_errors` outputs into one row per
    populated lead-time bucket: `{"lead_bucket": int, "samples": int,
    "avg_error_sec": float, "avg_abs_error_sec": float}`, sorted by bucket
    index.

    `avg_error_sec` is signed (mean bias -- positive means early
    observations landing in this bucket tend to overestimate the eventual
    delay); `avg_abs_error_sec` is the mean size of the miss regardless of
    direction, so a bucket whose errors cancel out to ~0 on average can
    still be shown as consistently inaccurate.
    """
    sums: dict[int, dict] = {}
    for oe in observation_errors:
        b = sums.setdefault(oe["lead_bucket"], {"samples": 0, "sum_error_sec": 0, "sum_abs_error_sec": 0})
        b["samples"] += 1
        b["sum_error_sec"] += oe["error_sec"]
        b["sum_abs_error_sec"] += abs(oe["error_sec"])
    return [
        {
            "lead_bucket": bucket,
            "samples": b["samples"],
            "avg_error_sec": b["sum_error_sec"] / b["samples"],
            "avg_abs_error_sec": b["sum_abs_error_sec"] / b["samples"],
        }
        for bucket, b in sorted(sums.items())
    ]


def rows_to_lead_bucket_stats(rows: list[tuple]) -> list[dict]:
    """Consume `pipeline.db.build_prediction_accuracy_ch_sql`'s result rows
    end to end: for each stop event, compute every early observation's
    bucketed error against that event's final `dep_delay`, then merge across
    every event into one row per lead-time bucket (see
    :func:`aggregate_by_lead_bucket`).

    Each row must be shaped `(route_code, service_type, scheduled_time,
    trip_id, date, stop_sequence, final_dep_delay, observations)` --
    exactly `build_prediction_accuracy_ch_sql`'s SELECT list.
    `final_dep_delay` (the query's own `argMax`-resolved value) is read only
    for documentation/consistency, not used directly: `observations` already
    contains every raw row for the group (including the one `argMax` picked
    as final), so :func:`compute_stop_event_errors` re-derives "final" from
    `observations` itself, the same way it would for any other caller.
    `observations` is a ClickHouse `groupArray` of `(captured_at,
    dep_delay)` pairs.
    """
    all_errors: list[dict] = []
    for row in rows:
        scheduled_time, trip_date, observations = row[2], row[4], row[7]
        scheduled_departure = scheduled_departure_at(trip_date, scheduled_time)
        all_errors.extend(compute_stop_event_errors(scheduled_departure, list(observations)))
    return aggregate_by_lead_bucket(all_errors)
