"""Comparing model variants backtested on the same origins: each one's error and
skill against B0, overall, per agency and by days ahead, its intervals, its
adoption verdict, and its per-origin wins over a baseline variant. A variant is
a fit (the ModelParams overrides in FITS) scored with one point forecast.
Readable without the `ml` dependency group."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ml.adoption import judge
from ml.backtest import HORIZONS
from ml.model_result import MODEL, SLOT_INTERVAL, IntervalStats, ModelBacktestResult, merged_intervals
from ml.summary import summarize

# A slot's window holds a handful of runs per weekday, so one run deep in the
# tail (a trip matched to a schedule an hour off, say) can set its mean. Each
# agency's 99th percentile bounds that pull and leaves the bulk of runs as is.
CLIP_QUANTILE = 0.99
FITS: dict[str, dict[str, Any]] = {
    "live": {},
    "clip": {"clip_quantile": CLIP_QUANTILE},
    "huber": {"mean_objective": "huber"},
    "clip-huber": {"clip_quantile": CLIP_QUANTILE, "mean_objective": "huber"},
}


def variant_name(fit: str, point: str) -> str:
    return fit if point == "mean" else f"{fit}-{point}"


def _on_model_coverage(result: ModelBacktestResult) -> float | None:
    total = IntervalStats()
    for agency, stats in result.intervals.get(MODEL, {}).items():
        if int(agency) not in result.fallback_agencies:
            total.merge(stats)
    return total.coverage


def _summary(result: ModelBacktestResult, baseline: ModelBacktestResult) -> dict[str, Any]:
    overall = summarize(result.agencies, MODEL)
    b0 = summarize(result.agencies, "B0")
    verdict = judge(result)
    wins = ties = losses = 0
    for (origin, skill), (base_origin, base_skill) in zip(result.origin_skill, baseline.origin_skill, strict=True):
        if origin != base_origin:
            raise ValueError(f"per-origin skill is out of step at {origin} and {base_origin}")
        if skill is None or base_skill is None:
            continue
        if skill > base_skill:
            wins += 1
        elif skill < base_skill:
            losses += 1
        else:
            ties += 1
    per_run, slot = merged_intervals(result, MODEL), merged_intervals(result, SLOT_INTERVAL)
    return {
        "params": result.params,
        "skill": overall.skill_vs_b0,
        "mae": overall.mae,
        "rmse": overall.rmse,
        "b0_mae": b0.mae,
        "b0_rmse": b0.rmse,
        "agency_skill": {
            str(r.agency_id): summarize(result.agencies, MODEL, agency_id=r.agency_id).skill_vs_b0
            for r in result.agencies
        },
        "horizon_mae": {str(h): summarize(result.agencies, MODEL, horizon=h).mae for h in HORIZONS},
        "horizon_b0_mae": {str(h): summarize(result.agencies, "B0", horizon=h).mae for h in HORIZONS},
        "coverage": per_run.coverage,
        "on_model_coverage": _on_model_coverage(result),
        "slot_coverage": slot.coverage,
        "mean_width": per_run.mean_width,
        "adopted": verdict.adopted,
        "reasons": list(verdict.reasons),
        "origin_wins": {"wins": wins, "ties": ties, "losses": losses},
    }


def compare(results: Mapping[str, ModelBacktestResult], *, baseline: str) -> dict[str, Any]:
    """Refuses variants scored on different origins, whose errors would not be
    paired, and a baseline that is not among them."""
    if baseline not in results:
        raise ValueError(f"baseline variant {baseline!r} is not among {sorted(results)}")
    base = results[baseline]
    for name, result in results.items():
        if result.origins != base.origins:
            raise ValueError(f"variant {name!r} was scored on different origins from {baseline!r}")
    return {
        "baseline": baseline,
        "origins": [origin.isoformat() for origin in base.origins],
        "cutoffs": [cutoff.isoformat() for cutoff in base.cutoffs],
        "fallback_agencies": base.fallback_agencies,
        "variants": {name: _summary(result, base) for name, result in sorted(results.items())},
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def _share(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_markdown(comparison: Mapping[str, Any]) -> str:
    variants: Mapping[str, Mapping[str, Any]] = comparison["variants"]
    names = list(variants)
    agencies = sorted({agency for v in variants.values() for agency in v["agency_skill"]}, key=int)
    origins = comparison["origins"]
    lines = [
        f"Origins {origins[0]} – {origins[-1]} ({len(origins)}), retrained at {', '.join(comparison['cutoffs'])}.",
        f"Kept on B0 at some cutoff: {', '.join(map(str, comparison['fallback_agencies'])) or 'none'}.",
        f"Wins, ties and losses count origins where a variant's skill beats {comparison['baseline']}'s.",
        "",
        "| variant | skill vs B0 | MAE | RMSE | p10–p90 per run | on-model | per slot | width | origins W/T/L "
        "| adopted |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in names:
        v = variants[name]
        wins = v["origin_wins"]
        lines.append(
            f"| {name} | {_pct(v['skill'])} | {_num(v['mae'])} | {_num(v['rmse'])} | {_share(v['coverage'])} "
            f"| {_share(v['on_model_coverage'])} | {_share(v['slot_coverage'])} | {_num(v['mean_width'])} "
            f"| {wins['wins']}/{wins['ties']}/{wins['losses']} | {'yes' if v['adopted'] else 'no'} |"
        )
    first = variants[names[0]]
    lines.append(f"\nB0: MAE {_num(first['b0_mae'])}, RMSE {_num(first['b0_rmse'])}.\n")
    lines.append("| variant | " + " | ".join(f"agency {a}" for a in agencies) + " |")
    lines.append("|---|" + "---|" * len(agencies))
    for name in names:
        lines.append(f"| {name} | " + " | ".join(_pct(variants[name]["agency_skill"][a]) for a in agencies) + " |")
    horizons = list(first["horizon_mae"])
    lines.append("\n| MAE by days ahead | " + " | ".join(horizons) + " |")
    lines.append("|---|" + "---|" * len(horizons))
    lines.append("| B0 | " + " | ".join(_num(first["horizon_b0_mae"][h]) for h in horizons) + " |")
    for name in names:
        lines.append(f"| {name} | " + " | ".join(_num(variants[name]["horizon_mae"][h]) for h in horizons) + " |")
    for name in names:
        if variants[name]["reasons"]:
            lines.append(f"\n{name} not adopted: {'; '.join(variants[name]['reasons'])}.")
    return "\n".join(lines) + "\n"
