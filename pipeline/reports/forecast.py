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
