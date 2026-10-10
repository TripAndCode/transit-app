"""Whether a model may replace B0: at least MIN_SKILL better overall, no agency
worse than B0, and an interval that holds its runs within COVERAGE_RANGE.
Readable without the `ml` dependency group."""

from __future__ import annotations

from dataclasses import dataclass

from ml.model_result import ModelBacktestResult, merged_intervals
from ml.summary import summarize

MIN_SKILL = 0.05
COVERAGE_RANGE = (0.75, 0.85)
MODEL = "LGBM"


@dataclass(frozen=True)
class Verdict:
    adopted: bool
    reasons: tuple[str, ...]


def judge_values(skill: float | None, agency_skills: dict[int, float | None], coverage: float | None) -> Verdict:
    reasons: list[str] = []
    if skill is None or skill < MIN_SKILL:
        shown = "unmeasured" if skill is None else f"{skill:.1%}"
        reasons.append(f"overall skill against B0 is {shown}, below {MIN_SKILL:.0%}")
    worse = sorted(agency for agency, s in agency_skills.items() if s is not None and s < 0)
    if worse:
        reasons.append("worse than B0 for agency " + ", ".join(str(agency) for agency in worse))
    low, high = COVERAGE_RANGE
    if coverage is None or not low <= coverage <= high:
        shown = "unmeasured" if coverage is None else f"{coverage:.1%}"
        reasons.append(f"p10–p90 holds {shown} of runs, outside {low:.0%}–{high:.0%}")
    return Verdict(adopted=not reasons, reasons=tuple(reasons))


def judge(result: ModelBacktestResult) -> Verdict:
    overall = summarize(result.agencies, MODEL).skill_vs_b0
    per_agency = {
        r.agency_id: summarize(result.agencies, MODEL, agency_id=r.agency_id).skill_vs_b0 for r in result.agencies
    }
    return judge_values(overall, per_agency, merged_intervals(result, MODEL).coverage)
