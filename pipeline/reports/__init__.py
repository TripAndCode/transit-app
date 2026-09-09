"""Live report computations, split by surface.

- filters:  shared SQL fragment builders (dedup CTE, ctx filters)
- rankings: the seven report-tab compute_* functions
- overview: the 概況 magazine payload + its helpers

Public API is re-exported here so ``from pipeline.reports import compute_X``
(used by api/routers and pipeline/query) keeps working unchanged.
"""

from pipeline.reports.council import (
    DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC,
    compute_council_summary,
    compute_delay_certificate,
)
from pipeline.reports.definition import (
    DefinitionMeta,
    format_definition_csv_line,
    format_definition_footnotes,
    resolve_definition_meta,
)
from pipeline.reports.dwell_run import compute_dwell_run_decomposition
from pipeline.reports.headway_quality import compute_headway_quality
from pipeline.reports.overview import compute_overview_summary
from pipeline.reports.performance_standard import compute_performance_standards, simulation_disclaimer
from pipeline.reports.rankings import (
    ON_TIME_PRESETS,
    compute_compare_ranking,
    compute_dow_ranking,
    compute_hourly_heatmap,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
)
from pipeline.reports.weather import compute_rain_delay, observation_disclaimer

__all__ = [
    "DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC",
    "ON_TIME_PRESETS",
    "DefinitionMeta",
    "compute_compare_ranking",
    "compute_council_summary",
    "compute_delay_certificate",
    "compute_dow_ranking",
    "compute_dwell_run_decomposition",
    "compute_headway_quality",
    "compute_hourly_heatmap",
    "compute_on_time",
    "compute_overview_summary",
    "compute_performance_standards",
    "compute_rain_delay",
    "compute_ranking",
    "compute_trend_series",
    "compute_worst_5min",
    "format_definition_csv_line",
    "format_definition_footnotes",
    "observation_disclaimer",
    "resolve_definition_meta",
    "simulation_disclaimer",
]
