"""SQL aggregations backing the 3 dashboard cards on the Ask tab's empty-thread state.

- delay_heatmap: top-N routes × dimension (DOW or hour-band) avg-delay grid
- anomaly_timeline: 30-day daily avg + std-deviation outliers
- movers: top-N routes by |Δ avg-delay| current-window vs prior-window
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import asyncpg

from api.range import RangeCtx

# --- shared dedup ---
_DEDUPED_CTE = """
WITH deduped AS (
    SELECT DISTINCT ON (route_code, service_type, scheduled_time, trip_id,
                        captured_at::date, stop_sequence)
        route_code, service_type, scheduled_time, captured_at, dep_delay
    FROM updates
    WHERE agency_id = $1
      AND dep_delay IS NOT NULL
      AND captured_at::date BETWEEN $2 AND $3
    ORDER BY route_code, service_type, scheduled_time, trip_id,
             captured_at::date, stop_sequence, captured_at DESC
)
"""


@dataclass(frozen=True)
class DelayHeatmap:
    routes: list[dict[str, Any]]  # [{route_code, label}, ...]
    dimensions: list[str]  # column labels (DOW names or hour bands)
    cells: list[list[float | None]]  # routes × dimensions; None where no data
    baseline_min: float  # 1.0 (the on-time threshold) — for diverging color scale


@dataclass(frozen=True)
class AnomalyTimeline:
    series: list[dict[str, Any]]  # [{date, avg_delay}]
    mean: float
    std: float
    anomalies: list[dict[str, Any]]  # [{date, delta_sigma}]


@dataclass(frozen=True)
class Movers:
    rows: list[dict[str, Any]] = field(default_factory=list)


_DOW_LABELS = ["月", "火", "水", "木", "金", "土", "日"]  # 0..6 (Mon..Sun)


async def delay_heatmap(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    dimension: str = "dow",
    top_routes: int = 20,
) -> DelayHeatmap:
    """Top-N routes × DOW or hour-band, avg delay (minutes) per cell.

    ``dimension``:
      - ``"dow"``     → 7 buckets (月..日)
      - ``"hour_band"`` → 4 buckets (朝 5-9, 昼 10-15, 夕 16-20, 夜 21-4)
    """
    if dimension not in ("dow", "hour_band"):
        raise ValueError(f"dimension must be 'dow' or 'hour_band', got {dimension!r}")

    # Step 1: top N routes by sample count.
    top_rows = await conn.fetch(
        f"{_DEDUPED_CTE}"
        "SELECT route_code, COUNT(*) AS samples "
        "FROM deduped GROUP BY route_code "
        "ORDER BY samples DESC, route_code LIMIT $4",
        agency_id,
        ctx.from_date,
        ctx.to_date,
        top_routes,
    )
    route_codes = [r["route_code"] for r in top_rows]
    if not route_codes:
        return DelayHeatmap(routes=[], dimensions=[], cells=[], baseline_min=1.0)

    # Step 2: route labels from static_routes
    labels = {
        r["route_code"]: (r["label"] or r["route_code"])
        for r in await conn.fetch(
            "SELECT route_id AS route_code, route_short_name AS label "
            "FROM static_routes WHERE agency_id = $1 AND route_id = ANY($2::text[])",
            agency_id,
            route_codes,
        )
    }

    # Step 3: aggregate by route × dimension
    if dimension == "dow":
        bucket_sql = "EXTRACT(DOW FROM captured_at)::int AS bucket"
        n_buckets = 7
        labels_list = _DOW_LABELS

        # Postgres DOW: 0=Sun..6=Sat; remap to 0=Mon..6=Sun
        def bucket_normalize(b: int) -> int:
            return (b + 6) % 7
    else:
        # hour-band
        bucket_sql = (
            "CASE "
            "WHEN EXTRACT(HOUR FROM captured_at)::int BETWEEN 5 AND 9 THEN 0 "
            "WHEN EXTRACT(HOUR FROM captured_at)::int BETWEEN 10 AND 15 THEN 1 "
            "WHEN EXTRACT(HOUR FROM captured_at)::int BETWEEN 16 AND 20 THEN 2 "
            "ELSE 3 END AS bucket"
        )
        n_buckets = 4
        labels_list = ["朝", "昼", "夕", "夜"]

        def bucket_normalize(b: int) -> int:
            return b

    grid_rows = await conn.fetch(
        f"{_DEDUPED_CTE}"
        f"SELECT route_code, {bucket_sql}, AVG(dep_delay)/60.0 AS avg_min, COUNT(*) AS n "
        f"FROM deduped WHERE route_code = ANY($4::text[]) "
        f"GROUP BY route_code, bucket",
        agency_id,
        ctx.from_date,
        ctx.to_date,
        route_codes,
    )

    by_route: dict[str, dict[int, float | None]] = {rc: {} for rc in route_codes}
    for r in grid_rows:
        b = bucket_normalize(r["bucket"])
        by_route[r["route_code"]][b] = float(r["avg_min"]) if r["avg_min"] is not None else None

    cells: list[list[float | None]] = [[by_route[rc].get(b) for b in range(n_buckets)] for rc in route_codes]
    routes = [{"route_code": rc, "label": labels.get(rc, rc)} for rc in route_codes]

    return DelayHeatmap(
        routes=routes,
        dimensions=labels_list,
        cells=cells,
        baseline_min=1.0,
    )


async def anomaly_timeline(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    days: int = 30,
    sigma: float = 2.0,
) -> AnomalyTimeline:
    """30-day daily avg delay (min) + outlier days flagged at ±sigma."""
    rows = await conn.fetch(
        f"{_DEDUPED_CTE}"
        "SELECT captured_at::date AS d, AVG(dep_delay)/60.0 AS avg_min "
        "FROM deduped GROUP BY d ORDER BY d",
        agency_id,
        ctx.from_date,
        ctx.to_date,
    )
    series = [
        {
            "date": r["d"].isoformat(),
            "avg_delay": float(r["avg_min"]) if r["avg_min"] is not None else 0.0,
        }
        for r in rows
    ]
    if len(series) < 2:
        return AnomalyTimeline(series=series, mean=0.0, std=0.0, anomalies=[])
    vals = [s["avg_delay"] for s in series]
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    anomalies = []
    if std > 0:
        for s in series:
            ds = (s["avg_delay"] - mean) / std
            if abs(ds) >= sigma:
                anomalies.append({"date": s["date"], "delta_sigma": round(ds, 2)})
    return AnomalyTimeline(series=series, mean=round(mean, 3), std=round(std, 3), anomalies=anomalies)


async def movers(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    window_days: int = 7,
    top: int = 10,
) -> Movers:
    """Top N routes by |Δ avg-delay (min)|: current window vs prior equal-length window."""
    # current window = [ctx.to_date - (window_days-1), ctx.to_date]
    # prior   window = [ctx.to_date - (2*window_days-1), ctx.to_date - window_days]
    cur_from = ctx.to_date - timedelta(days=window_days - 1)
    cur_to = ctx.to_date
    prv_from = ctx.to_date - timedelta(days=2 * window_days - 1)
    prv_to = ctx.to_date - timedelta(days=window_days)

    rows = await conn.fetch(
        """
        WITH cur AS (
            SELECT route_code, AVG(dep_delay)/60.0 AS avg_min, COUNT(*) AS n
            FROM updates
            WHERE agency_id = $1 AND dep_delay IS NOT NULL
              AND captured_at::date BETWEEN $2 AND $3
            GROUP BY route_code
        ),
        prv AS (
            SELECT route_code, AVG(dep_delay)/60.0 AS avg_min
            FROM updates
            WHERE agency_id = $1 AND dep_delay IS NOT NULL
              AND captured_at::date BETWEEN $4 AND $5
            GROUP BY route_code
        )
        SELECT cur.route_code,
               cur.avg_min  AS current_avg,
               prv.avg_min  AS previous_avg,
               cur.avg_min - COALESCE(prv.avg_min, 0) AS delta,
               cur.n        AS samples
        FROM cur LEFT JOIN prv USING (route_code)
        ORDER BY ABS(cur.avg_min - COALESCE(prv.avg_min, 0)) DESC NULLS LAST
        LIMIT $6
        """,
        agency_id,
        cur_from,
        cur_to,
        prv_from,
        prv_to,
        top,
    )
    label_rows = await conn.fetch(
        "SELECT route_id, route_short_name FROM static_routes WHERE agency_id = $1",
        agency_id,
    )
    labels = {r["route_id"]: (r["route_short_name"] or r["route_id"]) for r in label_rows}
    out_rows = []
    for r in rows:
        rc = r["route_code"]
        cur_v = float(r["current_avg"]) if r["current_avg"] is not None else None
        prv_v = float(r["previous_avg"]) if r["previous_avg"] is not None else None
        delta = float(r["delta"]) if r["delta"] is not None else 0.0
        pct = (delta / prv_v * 100.0) if prv_v else None
        out_rows.append(
            {
                "route_code": rc,
                "label": labels.get(rc, rc),
                "current_avg": cur_v,
                "previous_avg": prv_v,
                "delta": delta,
                "delta_pct": pct,
                "samples": int(r["samples"]),
            }
        )
    return Movers(rows=out_rows)
