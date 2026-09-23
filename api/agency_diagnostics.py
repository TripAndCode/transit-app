"""SQL and pure shaping for the admin agency diagnostics payload.

Split out of the router so every statement is one testable constant and
every shaping rule is a function over already-fetched rows: the router owns
the round trips, this module owns what is asked for and what the answer
means.

Scope note on RT coverage: ``rt_field_coverage_probes`` records the four
per-``stop_time_update`` optional fields of the TripUpdate feed
(``RT_COVERAGE_FIELDS``). No VehiclePosition or Alerts feed is probed,
ingested, or stored anywhere in this pipeline, so this payload reports the
fields that exist rather than inventing an entity/feed split the registry
cannot answer for.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

__all__ = [
    "AGENCIES_HEALTH_SQL",
    "AGENCY_HEADER_SQL",
    "ALL_CLAMP_HISTORY_SQL",
    "ALL_RT_COVERAGE_SQL",
    "CLAMP_HISTORY_DAYS",
    "CLAMP_HISTORY_SQL",
    "CURRENT_STATIC_VERSION_SQL",
    "DELETE_DEFAULT_WEIGHT_SQL",
    "DELETE_ROUTE_WEIGHT_SQL",
    "DELETE_STANDARD_SQL",
    "METRIC_TYPES",
    "RT_COVERAGE_FIELDS",
    "RT_COVERAGE_SQL",
    "STANDARDS_SQL",
    "STATIC_VERSIONS_SQL",
    "UPSERT_DEFAULT_WEIGHT_SQL",
    "UPSERT_ROUTE_WEIGHT_SQL",
    "UPSERT_STANDARD_SQL",
    "WEATHER_STATION_SQL",
    "WEIGHTS_COVERAGE_SQL",
    "WEIGHTS_SQL",
    "build_agency_health",
    "build_clamp_history",
    "build_rt_coverage",
    "build_static_versions",
    "build_weights_coverage",
    "freshness_state",
    "validate_standard_edits",
    "validate_weight_edits",
]

#: Mirrors the CHECK constraint on ``route_performance_standards.metric_type``.
#: Kept as an explicit allow-list so a bad value is a 422 with a readable
#: message instead of a constraint violation surfacing as a 500.
METRIC_TYPES = ("ewt_sec", "vehicle_km_delivered_pct")

#: Width of the clamp-rate sparkline, in whole days ending today.
CLAMP_HISTORY_DAYS = 14


AGENCY_HEADER_SQL = """
    SELECT a.agency_id, a.agency_name, a.feed_url, a.static_url,
           a.ingest_strategy, a.deleted_at,
           m.analyzed_at, m.max_updates_captured_at,
           (SELECT MAX(date) FROM agg_route_daily r WHERE r.agency_id = a.agency_id) AS latest_data_date
    FROM agencies a
    LEFT JOIN agg_meta m ON m.agency_id = a.agency_id
    WHERE a.agency_id = $1
"""

RT_COVERAGE_SQL = """
    SELECT field_name, confirmed, coverage, sample_size, source_feed, probed_at, expires_at
    FROM rt_field_coverage_probes
    WHERE agency_id = $1
"""

CLAMP_HISTORY_SQL = """
    SELECT date, raw_samples, clamp_count
    FROM agg_feed_health
    WHERE agency_id = $1 AND date >= $2 AND date <= $3
    ORDER BY date
"""

# `routes` and `calendar_until` are not columns of agg_static_version_summary
# and cannot be: static_loader replaces static_trips/static_calendar_dates
# wholesale on every load, so only the currently-loaded version still has
# raw rows to count. Past versions therefore carry NULL for both, which a
# reader must render as "not retained", never as zero.
STATIC_VERSIONS_SQL = """
    WITH current_version AS (
        SELECT t.static_version_id
        FROM static_trips t
        WHERE t.agency_id = $1 AND t.static_version_id IS NOT NULL
        LIMIT 1
    ),
    route_counts AS (
        SELECT t.static_version_id, count(DISTINCT t.route_id) AS routes
        FROM static_trips t
        WHERE t.agency_id = $1 AND t.static_version_id IS NOT NULL
        GROUP BY t.static_version_id
    ),
    calendar AS (
        SELECT max(c.date) AS calendar_until
        FROM static_calendar_dates c
        WHERE c.agency_id = $1
    )
    SELECT s.static_version_id AS version,
           s.computed_at       AS loaded_at,
           s.trip_count        AS trips,
           s.vehicle_km        AS vehicle_km,
           rc.routes           AS routes,
           CASE WHEN cv.static_version_id IS NOT NULL
                THEN (SELECT calendar_until FROM calendar) END AS calendar_until,
           (cv.static_version_id IS NOT NULL) AS is_current
    FROM agg_static_version_summary s
    LEFT JOIN route_counts rc ON rc.static_version_id = s.static_version_id
    LEFT JOIN current_version cv ON cv.static_version_id = s.static_version_id
    WHERE s.agency_id = $1
    ORDER BY s.computed_at DESC
"""

# The list view's per-row health, in four fleet-wide queries rather than one
# bundle per agency: the newest aggregated day is a single grouped pass over
# agg_route_daily, not a correlated MAX() re-scanned for every row.
AGENCIES_HEALTH_SQL = """
    WITH agg AS (
        SELECT agency_id, MAX(date) AS latest_data_date
        FROM agg_route_daily
        GROUP BY agency_id
    )
    SELECT a.agency_id, a.agency_name, a.feed_url, a.ingest_strategy, a.deleted_at,
           m.analyzed_at, m.max_updates_captured_at, g.latest_data_date
    FROM agencies a
    LEFT JOIN agg_meta m ON m.agency_id = a.agency_id
    LEFT JOIN agg g ON g.agency_id = a.agency_id
    ORDER BY a.agency_name
"""

ALL_RT_COVERAGE_SQL = """
    SELECT agency_id, field_name, confirmed, coverage, sample_size, probed_at, expires_at
    FROM rt_field_coverage_probes
"""

ALL_CLAMP_HISTORY_SQL = """
    SELECT agency_id, date, raw_samples, clamp_count
    FROM agg_feed_health
    WHERE date >= $1 AND date <= $2
    ORDER BY agency_id, date
"""

CURRENT_STATIC_VERSION_SQL = """
    SELECT DISTINCT ON (agency_id)
           agency_id, static_version_id AS version, computed_at AS loaded_at
    FROM agg_static_version_summary
    ORDER BY agency_id, computed_at DESC
"""

WEATHER_STATION_SQL = """
    SELECT station_id, station_name, source, note
    FROM agency_weather_stations
    WHERE agency_id = $1
"""

STANDARDS_SQL = """
    SELECT route_code, metric_type, threshold_value, bonus_malus_rate
    FROM route_performance_standards
    WHERE agency_id = $1
    ORDER BY route_code, metric_type
"""

WEIGHTS_SQL = """
    SELECT route_code, weight
    FROM ridership_weights
    WHERE agency_id = $1
    ORDER BY route_code NULLS FIRST
"""

# routes_total is counted over agg_route_daily, the same route_code grain the
# weighted on-time report joins weights against -- not static_routes, whose
# route_id is a different identifier.
WEIGHTS_COVERAGE_SQL = """
    SELECT
        (SELECT count(*) FROM ridership_weights w
          WHERE w.agency_id = $1 AND w.route_code IS NOT NULL) AS routes_with_weights,
        (SELECT count(DISTINCT r.route_code) FROM agg_route_daily r
          WHERE r.agency_id = $1) AS routes_total
"""

UPSERT_STANDARD_SQL = """
    INSERT INTO route_performance_standards
        (agency_id, route_code, metric_type, threshold_value, bonus_malus_rate)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (agency_id, route_code, metric_type) DO UPDATE SET
        threshold_value  = EXCLUDED.threshold_value,
        bonus_malus_rate = EXCLUDED.bonus_malus_rate
"""

DELETE_STANDARD_SQL = """
    DELETE FROM route_performance_standards
    WHERE agency_id = $1 AND route_code = $2 AND metric_type = $3
"""

# ridership_weights has two PARTIAL unique indexes rather than one table
# constraint, so each upsert has to restate the matching predicate for
# Postgres to infer the right index.
UPSERT_ROUTE_WEIGHT_SQL = """
    INSERT INTO ridership_weights (agency_id, route_code, weight)
    VALUES ($1, $2, $3)
    ON CONFLICT (agency_id, route_code) WHERE route_code IS NOT NULL
    DO UPDATE SET weight = EXCLUDED.weight
"""

UPSERT_DEFAULT_WEIGHT_SQL = """
    INSERT INTO ridership_weights (agency_id, route_code, weight)
    VALUES ($1, NULL, $2)
    ON CONFLICT (agency_id) WHERE route_code IS NULL
    DO UPDATE SET weight = EXCLUDED.weight
"""

DELETE_ROUTE_WEIGHT_SQL = "DELETE FROM ridership_weights WHERE agency_id = $1 AND route_code = $2"

DELETE_DEFAULT_WEIGHT_SQL = "DELETE FROM ridership_weights WHERE agency_id = $1 AND route_code IS NULL"


def freshness_state(latest_data_date: date | None, today: date) -> str:
    """``"fresh"`` / ``"stale"`` / ``"unknown"`` from the newest aggregated day.

    Deliberately Postgres-only: it compares the newest aggregated day against
    the calendar rather than against the newest live ClickHouse poll the way
    :func:`pipeline.freshness.is_stale` does. Today is counted as fresh
    because a day in progress is not yet aggregatable, so the boundary is
    yesterday.
    """
    if latest_data_date is None:
        return "unknown"
    return "fresh" if latest_data_date >= today - timedelta(days=1) else "stale"


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def build_rt_coverage(rows: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    """Shape the probe registry into one verdict per ``RT_COVERAGE_FIELDS``.

    Every field is always present in the output, so "never probed" and
    "probed and refuted" stay distinguishable (``probed``) instead of
    collapsing into a missing key. An expired verdict reads exactly like an
    absent one for ``present``, matching the read-side gate in
    ``pipeline.strategies.static_join``.
    """
    by_field = {r["field_name"]: r for r in rows}
    fields: dict[str, Any] = {}
    probed_times: list[datetime] = []
    for name in RT_COVERAGE_FIELDS:
        row = by_field.get(name)
        if row is None:
            fields[name] = {
                "present": False,
                "coverage_pct": None,
                "sample_size": None,
                "probed_at": None,
                "expired": False,
                "probed": False,
            }
            continue
        expires_at = row.get("expires_at")
        expired = expires_at is not None and expires_at <= now
        coverage = row.get("coverage")
        if row.get("probed_at") is not None:
            probed_times.append(row["probed_at"])
        fields[name] = {
            "present": bool(row.get("confirmed")) and not expired,
            "coverage_pct": None if coverage is None else round(float(coverage) * 100, 1),
            "sample_size": row.get("sample_size"),
            "probed_at": _iso(row.get("probed_at")),
            "expired": expired,
            "probed": True,
        }
    return {
        "fields": fields,
        "complete": all(f["present"] for f in fields.values()),
        "last_probed_at": _iso(max(probed_times)) if probed_times else None,
    }


def build_clamp_history(
    rows: Sequence[Mapping[str, Any]], today: date, days: int = CLAMP_HISTORY_DAYS
) -> list[dict[str, Any]]:
    """One entry per day across the whole window, oldest first.

    A day with no ``agg_feed_health`` row, or one whose ``raw_samples`` is
    zero, reports ``None`` rather than ``0.0``: "nothing was observed" is not
    "nothing was implausible", and a sparkline must not draw a healthy zero
    for a day the feed was down.
    """
    by_date = {r["date"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        row = by_date.get(day)
        raw = int(row["raw_samples"]) if row and row["raw_samples"] else 0
        clamp = int(row["clamp_count"]) if row and row["clamp_count"] else 0
        out.append({"date": day.isoformat(), "clamp_pct": round(clamp / raw * 100, 2) if raw else None})
    return out


def _calendar_date(value: str | None) -> str | None:
    """GTFS ``calendar_dates.date`` is stored as the feed's own ``YYYYMMDD``
    text; render it as an ISO date so the UI never has to parse two shapes."""
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def build_static_versions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape ``STATIC_VERSIONS_SQL`` rows into the drawer's version timeline,
    preserving the query's newest-first order."""
    return [
        {
            "version": r["version"],
            "loaded_at": _iso(r.get("loaded_at")),
            "trips": r.get("trips"),
            "vehicle_km": r.get("vehicle_km"),
            "routes": r.get("routes"),
            "calendar_until": _calendar_date(r.get("calendar_until")),
            "is_current": bool(r.get("is_current")),
        }
        for r in rows
    ]


def build_weights_coverage(routes_with_weights: int | None, routes_total: int | None) -> dict[str, int]:
    """How many of the agency's routes carry their own ridership weight."""
    return {
        "routes_with_weights": int(routes_with_weights or 0),
        "routes_total": int(routes_total or 0),
    }


def validate_standard_edits(items: Sequence[Mapping[str, Any]]) -> None:
    """Raise ``ValueError`` for a standards payload the table would reject.

    Checked here rather than left to the database so a bad edit is a 422
    naming the offending field, and so the in-payload duplicate case (which
    no constraint can see, because the rows are applied one at a time) is
    caught before any of them is written.
    """
    seen: set[tuple[str, str]] = set()
    for item in items:
        route_code = (item.get("route_code") or "").strip()
        if not route_code:
            raise ValueError("route_code must not be blank")
        metric_type = item.get("metric_type")
        if metric_type not in METRIC_TYPES:
            raise ValueError(f"metric_type must be one of {', '.join(METRIC_TYPES)}")
        threshold = item.get("threshold_value")
        # DOUBLE PRECISION accepts NaN and Infinity, and every bonus/malus
        # figure derived from such a threshold would silently be NaN.
        if threshold is None or not math.isfinite(float(threshold)):
            raise ValueError("threshold_value must be a finite number")
        rate = item.get("bonus_malus_rate")
        if rate is None or not math.isfinite(float(rate)) or float(rate) < 0:
            raise ValueError("bonus_malus_rate must be a finite, non-negative number")
        key = (route_code, str(metric_type))
        if key in seen:
            raise ValueError(f"duplicate standard for route {route_code} / {metric_type}")
        seen.add(key)


def validate_weight_edits(items: Sequence[Mapping[str, Any]]) -> None:
    """Raise ``ValueError`` for a weights payload the table would reject.

    ``route_code = None`` is the agency default row (see migration 0035), so
    two of those collide just as two rows for one route do.
    """
    seen: set[str | None] = set()
    for item in items:
        raw_code = item.get("route_code")
        route_code = raw_code.strip() if isinstance(raw_code, str) else None
        if raw_code is not None and not route_code:
            raise ValueError("route_code must not be blank")
        weight = item.get("weight")
        if weight is None or float(weight) <= 0:
            raise ValueError("weight must be greater than zero")
        if route_code in seen:
            label = route_code if route_code is not None else "the agency default"
            raise ValueError(f"duplicate weight for {label}")
        seen.add(route_code)


def _group_by_agency(rows: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["agency_id"], []).append(row)
    return grouped


def build_agency_health(
    *,
    headers: Sequence[Mapping[str, Any]],
    probes: Sequence[Mapping[str, Any]],
    clamps: Sequence[Mapping[str, Any]],
    versions: Sequence[Mapping[str, Any]],
    today: date,
    now: datetime,
) -> list[dict[str, Any]]:
    """One row per agency for the list view's health columns.

    Takes four already-fetched fleet-wide result sets and pivots them by
    agency here, so the endpoint stays at four queries no matter how many
    agencies exist. An agency missing from any of the three diagnostic sets
    still gets a row -- a newly onboarded agency has no probes, no clamp
    history and no static version, and must appear in the table saying so.
    """
    probes_by_agency = _group_by_agency(probes)
    clamps_by_agency = _group_by_agency(clamps)
    versions_by_agency = {v["agency_id"]: v for v in versions}

    out: list[dict[str, Any]] = []
    for header in headers:
        aid = header["agency_id"]
        coverage = build_rt_coverage(probes_by_agency.get(aid, []), now=now)
        version = versions_by_agency.get(aid)
        captured_at = header.get("max_updates_captured_at")
        analyzed_at = header.get("analyzed_at")
        latest_data_date = header.get("latest_data_date")
        out.append(
            {
                "agency_id": aid,
                "agency_name": header["agency_name"],
                "feed_url": header["feed_url"],
                "ingest_strategy": header.get("ingest_strategy"),
                "deleted_at": _iso(header.get("deleted_at")),
                "freshness": freshness_state(latest_data_date, today),
                "latest_data_date": _iso(latest_data_date),
                "last_analyzed_at": _iso(analyzed_at),
                "last_capture_at": _iso(captured_at),
                "rt_coverage": {
                    "complete": coverage["complete"],
                    "present_count": sum(1 for f in coverage["fields"].values() if f["present"]),
                    "field_count": len(RT_COVERAGE_FIELDS),
                    "probed": any(f["probed"] for f in coverage["fields"].values()),
                    "last_probed_at": coverage["last_probed_at"],
                },
                "clamp_history": build_clamp_history(clamps_by_agency.get(aid, []), today=today),
                "static_version": (
                    None
                    if version is None
                    else {"version": version["version"], "loaded_at": _iso(version.get("loaded_at"))}
                ),
            }
        )
    return out
