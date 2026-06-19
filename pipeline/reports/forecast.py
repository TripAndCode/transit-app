"""Expected-delay heatmap: summarize agg_route_hour_dow into a day×hour grid.

Pure (no DB) so the grid-fill, low-confidence, and disclaimer logic are
unit-testable. The endpoint (api/routers/reports.py) does the SQL — pooling
agg_route_hour_dow across service types per (dow, hour) — and passes the
per-cell rows here.

This is a seasonal-naive baseline ("expected delay"), NOT a prediction — see
DELAY_ANALYSIS.md. Only the sample-weighted mean is reported: a weighted mean of
per-departure averages equals the pooled mean, so it is exact. Percentiles are
not reported (a weighted mean of per-bucket percentiles is not the pooled
percentile, and percentiles cannot be recovered from per-bucket percentiles).
"""

from collections.abc import Iterable, Mapping
from typing import Any

from api.triage import LOW_CONFIDENCE_SAMPLES

# Plain-language disclaimer — NO jargon (p90/percentile/baseline forbidden).
# Defines a "measurement" inline so `samples` is never ambiguous.
_DISCLAIMER: dict[tuple[str, str], str] = {
    ("heatmap", "ja"): (
        "曜日・時間帯ごとに、この路線の過去の遅れの計測記録から平均を出したものです"
        "（1回＝ある日のある停留所での1計測）。事故・天候・当日の運行状況は反映していません。"
    ),
    ("heatmap", "en"): (
        "Average delay by day of week and hour, from this route's past measurements "
        "(each = one stop, on one run, on one day). It does not account for incidents, "
        "weather, or today's conditions."
    ),
}


def _disclaimer(case: str, locale: str, **vars: Any) -> str:
    lang = locale if locale in ("ja", "en") else "ja"
    tpl = _DISCLAIMER.get((case, lang)) or _DISCLAIMER[(case, "ja")]
    # Guard like pipeline/query/tools.py::_summary: a template/var mismatch returns
    # the raw template rather than raising a 500 in user-facing output.
    try:
        return tpl.format(**vars)
    except (KeyError, IndexError):
        return tpl


def summarize_expected_delay_heatmap(
    rows: Iterable[Mapping[str, Any]],
    route: str,
    locale: str = "ja",
) -> dict[str, Any]:
    """Fill a full ISODOW(1..7) × hour(0..23) grid — 168 cells. Pure.

    `rows`: per-(dow,hour) pooled mappings with keys ``dow``, ``hour``,
    ``avg_min`` (may be None), ``samples``. The endpoint SQL sample-weights
    ``avg_min`` across service types, so it is already the exact pooled mean;
    this lays it on the grid (missing cells → null/0) and flags low confidence.
    No percentile (cannot pool per-bucket percentiles).
    """
    by = {(int(r["dow"]), int(r["hour"])): r for r in rows if r["avg_min"] is not None and r["samples"]}
    cells: list[dict[str, Any]] = []
    for d in range(1, 8):
        for h in range(24):
            r = by.get((d, h))
            n = int(r["samples"]) if r else 0
            cells.append(
                {
                    "dow": d,
                    "hour": h,
                    "expected_avg_min": round(float(r["avg_min"]), 1) if r else None,
                    "samples": n,
                    "low_confidence": 0 < n < LOW_CONFIDENCE_SAMPLES,
                }
            )
    return {"route": route, "cells": cells, "disclaimer": _disclaimer("heatmap", locale)}


# Ordered hour→band mapping. SINGLE SOURCE OF TRUTH for the time bands (the
# frontend BAND_ORDER mirrors only the keys/order, for labels — keep identical).
BANDS: list[tuple[str, range]] = [
    ("early", range(0, 6)),
    ("morning", range(6, 9)),
    ("midday", range(9, 16)),
    ("evening", range(16, 19)),
    ("night", range(19, 24)),
]
_HOUR_BAND = {h: key for key, hrs in BANDS for h in hrs}


def band_of(hour: int) -> str:
    """Band key for an hour 0..23."""
    return _HOUR_BAND[int(hour)]


def _pooled(pairs: list[tuple[float, int]]) -> tuple[float | None, int]:
    """(avg_min, samples) pairs -> exact pooled (mean, total_samples)."""
    n = sum(s for _, s in pairs)
    if not n:
        return None, 0
    return sum(v * s for v, s in pairs) / n, n


def summarize_agency_overview(
    grid_rows: Iterable[Mapping[str, Any]],
    route_rows: Iterable[Mapping[str, Any]],
    locale: str = "ja",
    top_n: int = 8,
) -> dict[str, Any]:
    """Agency-wide 7×band grid + worst-window + delay-ranked routes. Pure.

    `grid_rows`: per-(dow,hour) pooled mappings ``{dow,hour,avg_min,samples}``.
    `route_rows`: per-route mappings ``{route_code, route_name, avg_min, samples}``.
    Pooling is exact (a sample-weighted mean of per-bucket means is the pooled
    mean). The worst window excludes low-confidence buckets so a small-sample
    fluke can never headline. No percentile (cannot pool per-bucket percentiles).
    """
    # ── grid: pool hours into bands per (dow, band) ──────────────────────
    buckets: dict[tuple[int, str], list[tuple[float, int]]] = {}
    for r in grid_rows:
        if r["avg_min"] is None or not r["samples"]:
            continue
        key = (int(r["dow"]), band_of(int(r["hour"])))
        buckets.setdefault(key, []).append((float(r["avg_min"]), int(r["samples"])))

    grid: list[dict[str, Any]] = []
    worst: dict[str, Any] | None = None
    for d in range(1, 8):
        for band, _ in BANDS:
            mean, n = _pooled(buckets.get((d, band), []))
            low_conf = 0 < n < LOW_CONFIDENCE_SAMPLES
            grid.append(
                {
                    "dow": d,
                    "band": band,
                    "expected_avg_min": round(mean, 1) if mean is not None else None,
                    "samples": n,
                    "low_confidence": low_conf,
                }
            )
            if mean is not None and not low_conf and (worst is None or mean > worst["_m"]):
                worst = {"dow": d, "band": band, "expected_avg_min": round(mean, 1), "samples": n, "_m": mean}
    if worst is not None:
        worst.pop("_m")

    # ── routes: rank by delay desc, low-confidence last, cap at top_n ────
    routes: list[dict[str, Any]] = []
    for r in route_rows:
        if r["avg_min"] is None or not r["samples"]:
            continue
        n = int(r["samples"])
        routes.append(
            {
                "route_code": r["route_code"],
                "route_name": r["route_name"],
                "expected_avg_min": round(float(r["avg_min"]), 1),
                "samples": n,
                "low_confidence": n < LOW_CONFIDENCE_SAMPLES,
            }
        )
    routes.sort(key=lambda x: (x["low_confidence"], -x["expected_avg_min"]))
    routes = routes[:top_n]

    return {"grid": grid, "worst": worst, "routes": routes, "disclaimer": _disclaimer("heatmap", locale)}
