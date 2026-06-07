"""Live report computations, split by surface.

- filters:  shared SQL fragment builders (dedup CTE, ctx filters)
- rankings: the seven report-tab compute_* functions
- overview: the 概況 magazine payload + its helpers

Public API is re-exported here so ``from pipeline.reports import compute_X``
(used by api/routers and pipeline/query) keeps working unchanged.
"""

from pipeline.reports.overview import compute_overview_summary
from pipeline.reports.rankings import (
    compute_compare_ranking,
    compute_dow_ranking,
    compute_hourly_heatmap,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
)

__all__ = [
    "compute_compare_ranking",
    "compute_dow_ranking",
    "compute_hourly_heatmap",
    "compute_on_time",
    "compute_overview_summary",
    "compute_ranking",
    "compute_trend_series",
    "compute_worst_5min",
]
