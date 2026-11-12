"""The feed-quality scorecard's monthly grade, A to F, from one
``feed_quality_monthly`` row's stored figures (:mod:`pipeline.feed_quality`).

The grade is recomputed from the stored figures under the thresholds below
whenever it is read, and only the admin API reads it: it is shown to admins
only until operators have had the methodology's advance notice and right of
reply (docs/methodology.md, "Publication process").

Each criterion passes or fails on its own figure as the scorecard states it.
Each threshold is inclusive, but for field coverage's arr_delay band, which
excludes both its ends:

- ``rt_completeness``: trips reported are at least
  :data:`MIN_RT_COMPLETENESS_PCT` of those planned, Cal-ITP's realtime
  completeness bar of no more than 5% missing. Trips reported beyond the plan
  are the feed naming trips the plan does not, not a shortfall, so they pass.
  This bar grades a feed; the comparability gate's lower
  :data:`~pipeline.reports.comparability.MIN_COVERAGE_PCT` decides only
  whether its delays can be ranked.
- ``stale_polls``: at most :data:`MAX_STALE_POLLS_PCT` of the polls measured
  carried a message older than
  :data:`~pipeline.feed_quality.STALE_MESSAGE_AGE_SEC`, Cal-ITP's freshness
  threshold and the GTFS-RT validator's W008.
- ``clamp``: the implausible share is not high by the comparability gate's
  own check (:func:`~pipeline.reports.comparability.clamp_is_high`).
- ``field_coverage``: every field the app reads from the realtime feed
  (:data:`~pipeline.strategies.static_join.RT_COVERAGE_FIELDS`) is carried as
  the RT field-coverage probe confirms a feed carries it
  (:func:`~pipeline.strategies.static_join.assess_field_coverage`), so the
  figures that need those fields can be computed. arr_delay passes only on
  some but fewer than half of the updates: carried on every update, it is not
  the sparse arrival estimate the app reads it as.

The grade counts the criteria failed (:data:`GRADES`): A for none, then one
letter down for each, to F for all four. A month without the figure a
criterion needs has no grade and names that criterion with the scorecard's
reason, never a guess, while still naming the criteria its stated figures
fail. Stop-event completeness and the message-age quantiles are stated beside
the grade but decide none of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pipeline.reports.comparability import clamp_is_high
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, assess_field_coverage

Criterion = Literal["rt_completeness", "stale_polls", "clamp", "field_coverage"]
CRITERIA: tuple[Criterion, ...] = ("rt_completeness", "stale_polls", "clamp", "field_coverage")

#: The grade for each number of failed criteria, from none to all.
GRADES = ("A", "B", "C", "D", "F")

MIN_RT_COMPLETENESS_PCT = 95.0
MAX_STALE_POLLS_PCT = 5.0

# Each criterion's figure and the column naming why it is missing.
_FIGURES: dict[Criterion, tuple[str, str]] = {
    "rt_completeness": ("rt_completeness_pct", "rt_completeness_missing"),
    "stale_polls": ("message_age_stale_pct", "message_age_missing"),
    "clamp": ("clamp_pct", "clamp_missing"),
    "field_coverage": ("stop_id_pct", "field_coverage_missing"),
}


@dataclass(frozen=True)
class MonthGrade:
    """``grade`` is None exactly when ``missing`` names a criterion, each with
    the scorecard's reason its figure is missing; ``failing`` names the
    criteria the month's stated figures fail, in :data:`CRITERIA` order."""

    grade: str | None
    failing: tuple[Criterion, ...]
    missing: tuple[tuple[Criterion, str], ...]


def _passes(criterion: Criterion, row: Mapping[str, Any]) -> bool:
    if criterion == "rt_completeness":
        return float(row["rt_completeness_pct"]) >= MIN_RT_COMPLETENESS_PCT
    if criterion == "stale_polls":
        return float(row["message_age_stale_pct"]) <= MAX_STALE_POLLS_PCT
    if criterion == "clamp":
        return not clamp_is_high(float(row["clamp_pct"]))
    shares = {f"{field}_coverage": float(row[f"{field}_pct"]) / 100 for field in RT_COVERAGE_FIELDS}
    verdicts = assess_field_coverage({"stop_time_updates": row["stop_time_updates"], **shares})
    return verdicts is not None and all(verdicts.values())


def grade_month(row: Mapping[str, Any]) -> MonthGrade:
    """The grade of one ``feed_quality_monthly`` row. A figure missing with
    no reason stored beside it is a month built before the scorecard stored
    that figure, which reads ``not_recorded``."""
    failing: list[Criterion] = []
    missing: list[tuple[Criterion, str]] = []
    for criterion in CRITERIA:
        figure, reason = _FIGURES[criterion]
        if row.get(figure) is None:
            missing.append((criterion, row.get(reason) or "not_recorded"))
        elif not _passes(criterion, row):
            failing.append(criterion)
    grade = None if missing else GRADES[len(failing)]
    return MonthGrade(grade=grade, failing=tuple(failing), missing=tuple(missing))
