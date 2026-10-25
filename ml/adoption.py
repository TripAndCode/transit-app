"""Whether a model may replace B0: at least MIN_SKILL better overall, no agency
worse than B0, and an interval that holds its runs within COVERAGE_RANGE.
Readable without the `ml` dependency group."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ml.model_result import MODEL, ModelBacktestResult, merged_intervals
from ml.summary import summarize

MIN_SKILL = 0.05
COVERAGE_RANGE = (0.75, 0.85)

SKILL_BELOW_MIN = "skill_below_min"
AGENCY_WORSE = "agency_worse"
COVERAGE_OUT_OF_RANGE = "coverage_out_of_range"


@dataclass(frozen=True)
class Failure:
    """One reason a model was not adopted, as data the web app words itself:
    `value` is the measured figure (None when unmeasured) and `agencies` the
    ones scoring worse than B0."""

    code: str
    value: float | None = None
    agencies: tuple[int, ...] = ()


@dataclass(frozen=True)
class Verdict:
    adopted: bool
    failures: tuple[Failure, ...] = ()
    skill: float | None = None
    coverage: float | None = None
    agency_skills: Mapping[int, float | None] = field(default_factory=dict)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(_describe(failure) for failure in self.failures)


def _shown(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.1%}"


def _describe(failure: Failure) -> str:
    if failure.code == SKILL_BELOW_MIN:
        return f"overall skill against B0 is {_shown(failure.value)}, below {MIN_SKILL:.0%}"
    if failure.code == AGENCY_WORSE:
        return "worse than B0 for agency " + ", ".join(str(agency) for agency in failure.agencies)
    low, high = COVERAGE_RANGE
    return f"p10–p90 holds {_shown(failure.value)} of runs, outside {low:.0%}–{high:.0%}"


def judge_values(skill: float | None, agency_skills: dict[int, float | None], coverage: float | None) -> Verdict:
    failures: list[Failure] = []
    if skill is None or skill < MIN_SKILL:
        failures.append(Failure(SKILL_BELOW_MIN, skill))
    worse = tuple(sorted(agency for agency, s in agency_skills.items() if s is not None and s < 0))
    if worse:
        failures.append(Failure(AGENCY_WORSE, agencies=worse))
    low, high = COVERAGE_RANGE
    if coverage is None or not low <= coverage <= high:
        failures.append(Failure(COVERAGE_OUT_OF_RANGE, coverage))
    return Verdict(
        adopted=not failures,
        failures=tuple(failures),
        skill=skill,
        coverage=coverage,
        agency_skills=dict(agency_skills),
    )


def judge(result: ModelBacktestResult) -> Verdict:
    overall = summarize(result.agencies, MODEL).skill_vs_b0
    per_agency = {
        r.agency_id: summarize(result.agencies, MODEL, agency_id=r.agency_id).skill_vs_b0 for r in result.agencies
    }
    return judge_values(overall, per_agency, merged_intervals(result, MODEL).coverage)
