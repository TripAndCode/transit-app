"""Growable synthetic GTFS static+RT fixture generator (item 21).

Existing fixtures (``tests/fixtures/hiroden_static.zip`` etc.) and
``tests/pipeline/test_static_loader.py``/``test_static_join.py`` only cover
the *loading* step of the pipeline — nothing before this module verified
that ``agg_*`` table values (built by ``pipeline/analyze.py`` from GTFS-RT
``updates`` observations in ClickHouse) are numerically correct against
hand-computable expected results.

This module provides two things:

1. ``build_static_zip`` — a GTFS static zip builder. Hoisted from
   ``tests/pipeline/test_static_loader.py``'s original ``_make_zip`` (now a
   thin re-export there, see that file) so there is exactly one place that
   builds a minimal GTFS static zip for tests, extended with an optional
   ``stop_times_rows`` param the original never needed.
2. A small, growable set of named ``SyntheticPattern`` builder functions
   (``uniform_delays``, ``outlier_spike``, ``null_delays``). Each returns a
   fully self-contained dataset — a GTFS static fragment (routes/stops/
   trips/stop_times) plus matching synthetic ``updates`` rows — along with
   an ``expected`` dict of hand-computed ``agg_*`` values. That dict is the
   single source of truth for "what the right answer is" for this pattern;
   ``tests/pipeline/test_synthetic_agg_e2e.py`` (item 21) and later
   frontend/Ask-tab checks (items 22/23) both import it instead of each
   re-deriving their own expected numbers.

Add new patterns as new functions returning ``SyntheticPattern`` — do not
grow the existing three into a single parameterized mega-fixture; the whole
point of separate named functions is that a future item can add a fourth
pattern (e.g. multi-route, multi-day) without restructuring the existing
ones or their callers.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# ISODOW helper for the fixed dates below (see `date`/`dow` on each pattern) —
# `date -d 2026-06-01 +%u` => 1 (Monday). Kept as a literal, not computed via
# stdlib, because these are meant to be independently hand-verifiable values,
# not values re-derived by the same kind of code under test.


def build_static_zip(
    tmp_path,
    *,
    stops_rows: list[str] | None = None,
    trips_rows: list[str] | None = None,
    routes_rows: list[str] | None = None,
    stop_times_rows: list[str] | None = None,
    filename: str = "test_static.zip",
) -> str:
    """Build a minimal GTFS Static zip for testing.

    Same shape as the original ``_make_zip`` in
    ``tests/pipeline/test_static_loader.py`` (stops.txt/trips.txt/routes.txt,
    included only when their *_rows arg is not None), plus an optional
    ``stop_times.txt`` the original never wrote — needed here so synthetic
    RT `updates` rows can join to a real stop_id/stop_sequence pair the same
    way ``pipeline/analyze.py``'s has_static path does in production.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if stops_rows is not None:
            content = "stop_id,stop_name,stop_lat,stop_lon\n" + "\n".join(stops_rows)
            zf.writestr("stops.txt", content)
        if trips_rows is not None:
            content = "trip_id,route_id,trip_headsign,shape_id\n" + "\n".join(trips_rows)
            zf.writestr("trips.txt", content)
        if routes_rows is not None:
            content = "route_id,route_short_name\n" + "\n".join(routes_rows)
            zf.writestr("routes.txt", content)
        if stop_times_rows is not None:
            content = "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n" + "\n".join(stop_times_rows)
            zf.writestr("stop_times.txt", content)
    zip_path = tmp_path / filename
    zip_path.write_bytes(buf.getvalue())
    return str(zip_path)


@dataclass(frozen=True)
class SyntheticPattern:
    """One named, self-contained synthetic dataset.

    ``update_rows`` are ready to pass straight to
    ``pipeline.clickhouse.insert_updates(ch_client, agency_id, pattern.update_rows)``
    — each tuple is ``(file_name, captured_at, trip_id, service_type,
    scheduled_time, route_code, stop_sequence, dep_delay)``, matching
    ``pipeline.clickhouse.UPDATE_COLUMNS`` minus the ``agency_id`` that
    ``insert_updates`` itself prepends. ``captured_at`` is an ISO-8601 UTC
    string (``...Z``) — the same explicit-UTC-string convention already used
    by ``tests/api/test_api_map.py``'s direct ``insert_updates`` calls, which
    sidesteps clickhouse-connect resolving a *naive* datetime via the
    process-local host timezone (see ``pipeline/strategies/_pb.py``'s ``_ts``
    docstring).

    ``expected`` maps agg table name -> a dict of the hand-computed column
    values a row for this pattern's ``route_code``/``stop_id`` must have
    after ``analyze()`` runs. All delay values are chosen so every ratio in
    ``expected`` reduces to a clean, exactly-representable number (no
    recurring binary fractions), so equality assertions don't need a
    tolerance.
    """

    name: str
    route_code: str
    stop_id: str
    stop_name: str
    date: str  # JST calendar date "YYYY-MM-DD" every row lands on
    dow: int  # ISODOW of `date` (1=Mon..7=Sun)
    scheduled_time: str  # "HH:MM:SS"
    time_band: str  # matches api.range.time_band_case_sql's bucket for scheduled_time
    service_type: str
    routes_rows: list[str]
    stops_rows: list[str]
    trips_rows: list[str]
    stop_times_rows: list[str]
    update_rows: list[tuple]
    expected: dict


def load_pattern_static(pattern: SyntheticPattern, tmp_path, agency_id, pg_conn) -> None:
    """Build and load *pattern*'s static fragment via ``pipeline.static_loader.load_static``."""
    from pipeline.static_loader import load_static

    zip_path = build_static_zip(
        tmp_path,
        stops_rows=pattern.stops_rows,
        trips_rows=pattern.trips_rows,
        routes_rows=pattern.routes_rows,
        stop_times_rows=pattern.stop_times_rows,
        filename=f"{pattern.name}_static.zip",
    )
    load_static(zip_path, agency_id, pg_conn)


def insert_pattern_updates(pattern: SyntheticPattern, ch_client, agency_id: int) -> int:
    """Insert *pattern*'s synthetic ``updates`` rows into ClickHouse."""
    from pipeline.clickhouse import insert_updates

    return insert_updates(ch_client, agency_id, pattern.update_rows)


# UTC 2026-05-31T15:00:00 == JST 2026-06-01T00:00:00 (JST = UTC+9) — deliberately
# pinned to JST midnight, not an arbitrary UTC instant, so the full ~24h/86400s
# JST day is available as headroom below (an earlier version of this constant
# started mid-JST-day and only had ~14h of real headroom despite a comment
# claiming 23h — verified by hand against the UTC+9 offset, not re-derived by
# code under test).
_TS_BASE = datetime(2026, 5, 31, 15, 0, 0, tzinfo=timezone.utc)


def _ts(offset: int) -> str:
    """captured_at string for the *offset*-th synthetic row (offset in seconds).

    All patterns share the same UTC base instant (`_TS_BASE`, JST midnight),
    each row `offset` seconds after it, so every row gets a distinct, ordered
    `captured_at`. `offset` must stay under 86400 (24h) so the JST calendar
    date never rolls over past `_TS_BASE`'s day (which would split a pattern's
    rows across two `date` values and break its hand-computed expectations) —
    comfortably above any pattern size in practice, unlike a hardcoded 2-digit
    seconds field, which would silently produce malformed ISO-8601 past 60 rows.
    """
    return (_TS_BASE + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def uniform_delays() -> SyntheticPattern:
    """Pattern (a): uniform delays across every trip on one route.

    25 distinct trips, each observed exactly once, all with the same 30s
    departure delay. The mean, on-time rate, and every count-style column
    below are trivial arithmetic on a single repeated value.

    p50_min/p90_min are the one non-obvious value here: `agg_route_stats`'s
    percentile columns are built from Postgres's `PERCENT_RANK()` (see
    `pipeline/analyze.py`), which is `(rank - 1) / (row_count - 1)`. When
    EVERY row in the partition is tied at the same value, `RANK()` gives
    every row the same rank (1), so `PERCENT_RANK` is 0 for all of them —
    `WHERE pct >= 0.5`/`>= 0.9` then matches zero rows, and `MIN()` over an
    all-NULL `CASE` is NULL. A fully uniform distribution therefore
    genuinely yields a NULL p50/p90 under this codebase's rank-based
    formula (not 0.5, the tied value) — asserted as `None` below rather
    than avoided, since that NULL is itself the correct, hand-verified
    answer and a future switch to an interpolating percentile function
    would change it.
    """
    n = 25
    delay_sec = 30
    route_code = "SYN_UNIFORM"
    stop_id = "SYN_S_UNIFORM"
    stop_name = "均一テスト停留所"
    date = "2026-06-01"
    dow = 1
    scheduled_time = "08:00:00"
    time_band = "morning"
    service_type = "平日"

    trip_ids = [f"{route_code}_T{i}" for i in range(n)]
    routes_rows = [f"{route_code},均一テスト系統"]
    stops_rows = [f"{stop_id},{stop_name},40.0,140.0"]
    trips_rows = [f"{tid},{route_code},," for tid in trip_ids]
    stop_times_rows = [f"{tid},1,{stop_id},{scheduled_time},{scheduled_time}" for tid in trip_ids]
    update_rows = [
        (f"{route_code}_{i}.pb", _ts(i), tid, service_type, scheduled_time, route_code, 1, delay_sec)
        for i, tid in enumerate(trip_ids)
    ]

    expected = {
        "agg_route_stats": {
            "avg_min": 0.5,
            "p50_min": None,  # see docstring: fully-tied partition -> NULL, not 0.5
            "p90_min": None,
            "late_5min_plus": 0,
            "on_time_pct": 100.0,
            "late5_pct": 0.0,
            "samples": n,
        },
        "agg_route_hour": {"avg_min": 0.5, "p50_min": None, "p90_min": None, "samples": n},
        "agg_route_dow": {"avg_min": 0.5, "samples": n},
        "agg_route_hour_dow": {"avg_min": 0.5, "samples": n},
        "agg_daily_trend": {"avg_min": 0.5, "samples": n},
        "agg_route_daily": {
            "avg_delay_sec": 30,
            "worst_delay_sec": 30,
            "trips_observed": n,
            "samples": n,
        },
        "agg_hour_daily": {"hour": 8, "avg_min": 0.5, "samples": n},
        "agg_stop_seq": {"avg_min": 0.5, "samples": n},
        "agg_stop_daily": {"delay_sum": n * delay_sec, "samples": n},
        "agg_stop_routes": {"route_codes": route_code},
        "agg_feed_health": {"raw_samples": n, "clamp_count": 0},
    }

    return SyntheticPattern(
        name="uniform_delays",
        route_code=route_code,
        stop_id=stop_id,
        stop_name=stop_name,
        date=date,
        dow=dow,
        scheduled_time=scheduled_time,
        time_band=time_band,
        service_type=service_type,
        routes_rows=routes_rows,
        stops_rows=stops_rows,
        trips_rows=trips_rows,
        stop_times_rows=stop_times_rows,
        update_rows=update_rows,
        expected=expected,
    )


def outlier_spike() -> SyntheticPattern:
    """Pattern (b): one clear outlier trip amid otherwise-uniform data.

    24 trips at a 30s delay, plus 1 trip spiking to 600s (10 min — well
    under ``MAX_PLAUSIBLE_DELAY_SEC`` (7200s), so it is a real outlier, not a
    clamped/excluded implausible spike). With 24 of 25 values tied at the
    minimum, Postgres's ``PERCENT_RANK()`` puts every tied row at rank 0 and
    the single maximum at rank 1.0 — so BOTH p50 and p90 land exactly on the
    outlier (10.0 min), which is deliberate and doubles as a percentile
    mechanics regression check, not just a mean/outlier check.
    """
    n = 25
    normal_delay_sec = 30
    spike_delay_sec = 600
    route_code = "SYN_SPIKE"
    stop_id = "SYN_S_SPIKE"
    stop_name = "外れ値テスト停留所"
    date = "2026-06-01"
    dow = 1
    scheduled_time = "08:00:00"
    time_band = "morning"
    service_type = "平日"

    trip_ids = [f"{route_code}_T{i}" for i in range(n)]
    routes_rows = [f"{route_code},外れ値テスト系統"]
    stops_rows = [f"{stop_id},{stop_name},40.1,140.1"]
    trips_rows = [f"{tid},{route_code},," for tid in trip_ids]
    stop_times_rows = [f"{tid},1,{stop_id},{scheduled_time},{scheduled_time}" for tid in trip_ids]

    delays = [normal_delay_sec] * (n - 1) + [spike_delay_sec]
    update_rows = [
        (f"{route_code}_{i}.pb", _ts(i), tid, service_type, scheduled_time, route_code, 1, delay)
        for i, (tid, delay) in enumerate(zip(trip_ids, delays, strict=True))
    ]

    total_delay_sec = normal_delay_sec * (n - 1) + spike_delay_sec  # 720 + 600 = 1320
    avg_delay_sec = total_delay_sec / n  # 52.8

    expected = {
        "agg_route_stats": {
            "avg_min": 0.88,  # round(52.8 / 60, 2)
            "p50_min": 10.0,  # spike-dominated percentile, see docstring
            "p90_min": 10.0,
            "late_5min_plus": 1,  # only the 600s spike exceeds 300s
            "on_time_pct": 96.0,  # 24/25 <= 60s
            "late5_pct": 4.0,  # 1/25 > 300s
            "samples": n,
        },
        "agg_route_hour": {"avg_min": 0.88, "p50_min": 10.0, "p90_min": 10.0, "samples": n},
        "agg_route_dow": {"avg_min": 0.88, "samples": n},
        "agg_route_hour_dow": {"avg_min": 0.88, "samples": n},
        "agg_daily_trend": {"avg_min": 0.88, "samples": n},
        "agg_route_daily": {
            "avg_delay_sec": round(avg_delay_sec),  # ROUND(52.8)::int = 53
            "worst_delay_sec": spike_delay_sec,
            "trips_observed": n,
            "samples": n,
        },
        "agg_hour_daily": {"hour": 8, "avg_min": 0.88, "samples": n},
        "agg_stop_seq": {"avg_min": 0.88, "samples": n},
        "agg_stop_daily": {"delay_sum": total_delay_sec, "samples": n},
        "agg_stop_routes": {"route_codes": route_code},
        "agg_feed_health": {"raw_samples": n, "clamp_count": 0},
    }

    return SyntheticPattern(
        name="outlier_spike",
        route_code=route_code,
        stop_id=stop_id,
        stop_name=stop_name,
        date=date,
        dow=dow,
        scheduled_time=scheduled_time,
        time_band=time_band,
        service_type=service_type,
        routes_rows=routes_rows,
        stops_rows=stops_rows,
        trips_rows=trips_rows,
        stop_times_rows=stop_times_rows,
        update_rows=update_rows,
        expected=expected,
    )


def null_delays() -> SyntheticPattern:
    """Pattern (c): a route with some NULL/missing delay observations.

    22 trips carry a real 60s delay; 3 more trips carry a NULL `dep_delay`
    (an arrival-only StopTimeUpdate, or a degraded poll — see
    ``pipeline/db.py``'s ``build_dedup_ch_sql`` docstring). The shared dedup
    builder filters ``dep_delay IS NOT NULL`` before every delay-averaging
    aggregate, so those 3 rows must be excluded from every sample
    count/average below (22, not 25) without aborting `analyze()` — this
    exercises the pipeline's null-handling, not just its arithmetic.
    `agg_stop_routes` is intentionally NOT asserted to differ from the other
    two patterns: it is sourced from an unfiltered raw-key scan (see
    ``pipeline/analyze.py``), so it would show the same `route_codes` value
    with or without the NULL rows — a separate existing regression test
    (``test_analyze_agg_stop_routes_keeps_route_with_only_null_delay_observations``)
    already covers the case where EVERY observation for a key is NULL.

    The 22 valid observations are also all tied at the same 60s delay, so
    `agg_route_stats`/`agg_route_hour`'s p50_min/p90_min are NULL for the
    same rank-tie reason documented on `uniform_delays` above — not because
    of the NULL rows, but because the surviving (non-NULL) subset is itself
    uniform.
    """
    n_valid = 22
    n_null = 3
    n = n_valid + n_null
    delay_sec = 60
    route_code = "SYN_NULLMIX"
    stop_id = "SYN_S_NULLMIX"
    stop_name = "欠測テスト停留所"
    date = "2026-06-01"
    dow = 1
    scheduled_time = "08:00:00"
    time_band = "morning"
    service_type = "平日"

    trip_ids = [f"{route_code}_T{i}" for i in range(n)]
    routes_rows = [f"{route_code},欠測テスト系統"]
    stops_rows = [f"{stop_id},{stop_name},40.2,140.2"]
    trips_rows = [f"{tid},{route_code},," for tid in trip_ids]
    stop_times_rows = [f"{tid},1,{stop_id},{scheduled_time},{scheduled_time}" for tid in trip_ids]

    delays: list[int | None] = [delay_sec] * n_valid + [None] * n_null
    update_rows = [
        (f"{route_code}_{i}.pb", _ts(i), tid, service_type, scheduled_time, route_code, 1, delay)
        for i, (tid, delay) in enumerate(zip(trip_ids, delays, strict=True))
    ]

    expected = {
        "agg_route_stats": {
            "avg_min": 1.0,
            "p50_min": None,  # see docstring: the valid subset is itself fully tied -> NULL
            "p90_min": None,
            "late_5min_plus": 0,
            "on_time_pct": 100.0,
            "late5_pct": 0.0,
            "samples": n_valid,
        },
        "agg_route_hour": {"avg_min": 1.0, "p50_min": None, "p90_min": None, "samples": n_valid},
        "agg_route_dow": {"avg_min": 1.0, "samples": n_valid},
        "agg_route_hour_dow": {"avg_min": 1.0, "samples": n_valid},
        "agg_daily_trend": {"avg_min": 1.0, "samples": n_valid},
        "agg_route_daily": {
            "avg_delay_sec": delay_sec,
            "worst_delay_sec": delay_sec,
            "trips_observed": n_valid,
            "samples": n_valid,
        },
        "agg_hour_daily": {"hour": 8, "avg_min": 1.0, "samples": n_valid},
        "agg_stop_seq": {"avg_min": 1.0, "samples": n_valid},
        "agg_stop_daily": {"delay_sum": n_valid * delay_sec, "samples": n_valid},
        "agg_stop_routes": {"route_codes": route_code},
        "agg_feed_health": {"raw_samples": n_valid, "clamp_count": 0},
    }

    return SyntheticPattern(
        name="null_delays",
        route_code=route_code,
        stop_id=stop_id,
        stop_name=stop_name,
        date=date,
        dow=dow,
        scheduled_time=scheduled_time,
        time_band=time_band,
        service_type=service_type,
        routes_rows=routes_rows,
        stops_rows=stops_rows,
        trips_rows=trips_rows,
        stop_times_rows=stop_times_rows,
        update_rows=update_rows,
        expected=expected,
    )


# All patterns, for callers (e.g. a future item 22/23 fixture) that want to
# iterate every pattern rather than naming one.
ALL_PATTERNS = (uniform_delays, outlier_spike, null_delays)
