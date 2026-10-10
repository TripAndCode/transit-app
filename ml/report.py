"""The baselines' evaluation as one static HTML page: overall, by horizon,
peak against off-peak, sparse cells, and per agency, with coverage beside
every error so a method that abstains is seen to."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from html import escape

from ml.adoption import judge
from ml.backtest import HORIZONS, METHODS, SPARSE_RUNS, AgencyResult
from ml.model_result import ModelBacktestResult, merged_intervals
from ml.summary import summarize


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _horizon_chart(results: Sequence[AgencyResult], methods: Sequence[str] = METHODS) -> str:
    width, height, pad = 560, 220, 40
    series = {m: [summarize(results, m, horizon=h).mae for h in HORIZONS] for m in methods}
    values = [v for maes in series.values() for v in maes if v is not None]
    top = max(values, default=0.0) * 1.1 or 1.0
    xs = {h: pad + (width - 2 * pad) * (h - 1) / (len(HORIZONS) - 1) for h in HORIZONS}

    def y(value: float) -> float:
        return height - pad - (height - 2 * pad) * value / top

    lines = []
    for index, (method, maes) in enumerate(series.items()):
        points = " ".join(f"{xs[h]:.1f},{y(v):.1f}" for h, v in zip(HORIZONS, maes, strict=True) if v is not None)
        lines.append(f'<polyline class="s{index}" points="{points}"/>')
        lines.append(f'<text class="s{index}" x="{width - pad + 4}" y="{pad + 14 * index}">{method}</text>')
    ticks = "".join(f'<text x="{xs[h]:.1f}" y="{height - pad + 16}" text-anchor="middle">{h}</text>' for h in HORIZONS)
    return (
        f'<svg viewBox="0 0 {width + 40} {height}" role="img" aria-label="MAE by days ahead">'
        f'<line class="axis" x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}"/>'
        f'<text x="{pad}" y="{pad - 10}">MAE (min) by days ahead</text>{ticks}{"".join(lines)}</svg>'
    )


_HEAD = "<tr><th>method</th><th>MAE</th><th>RMSE</th><th>coverage</th><th>skill vs B0</th></tr>"


def _table(
    results: Sequence[AgencyResult],
    *,
    methods: Sequence[str] = METHODS,
    peak: bool | None = None,
    sparse: bool | None = None,
) -> str:
    rows = []
    for method in methods:
        s = summarize(results, method, peak=peak, sparse=sparse)
        rows.append(
            f"<tr><th>{method}</th><td>{_num(s.mae)}</td><td>{_num(s.rmse)}</td>"
            f"<td>{_pct(s.coverage)}</td><td>{_pct(s.skill_vs_b0)}</td></tr>"
        )
    return f"<table>{_HEAD}{''.join(rows)}</table>"


MODEL_METHODS = ("B0", "LGBM")


def _interval_rows(models: ModelBacktestResult) -> str:
    rows = []
    for method, label in (("LGBM", "LGBM p10–p90 (calibrated)"), ("B0", "B0 slot p10–p90")):
        stats = merged_intervals(models, method)
        pinball = " / ".join(_num(stats.mean_pinball(a)) for a in ("0.1", "0.5", "0.9"))
        rows.append(
            f"<tr><th>{label}</th><td>{_pct(stats.coverage)}</td>"
            f"<td>{_num(stats.mean_width)}</td><td>{pinball}</td></tr>"
        )
    head = "<tr><th>interval</th><th>runs inside</th><th>mean width</th><th>pinball 0.1 / 0.5 / 0.9</th></tr>"
    return f"<table>{head}{''.join(rows)}</table>"


def _fallback_note(models: ModelBacktestResult) -> str:
    if not models.fallback_agencies:
        return ""
    agencies = ", ".join(str(agency) for agency in models.fallback_agencies)
    return f"<p class='muted'>Kept on B0 for too little history at a cutoff: agency {agencies}.</p>"


def _model_section(models: ModelBacktestResult) -> str:
    verdict = judge(models)
    status = "adopted" if verdict.adopted else "not adopted: " + "; ".join(escape(r) for r in verdict.reasons)
    skills = sorted(s for _, s in models.origin_skill if s is not None)
    spread = (
        f"skill per origin ranges {_pct(skills[0])} to {_pct(skills[-1])}, median {_pct(skills[len(skills) // 2])}"
        if skills
        else "no origin had cells both predict"
    )
    agencies = "".join(f"<h3>Agency {r.agency_id}</h3>{_table([r], methods=MODEL_METHODS)}" for r in models.agencies)
    return (
        "<h2>Model against B0</h2>"
        f"<p class='muted'>Retrained once per week of origins ({len(models.cutoffs)} cutoffs), "
        f"{len(models.origins)} origins; {spread}. Verdict: {status}.</p>"
        f"{_fallback_note(models)}"
        f"{_table(models.agencies, methods=MODEL_METHODS)}"
        f"{_horizon_chart(models.agencies, methods=MODEL_METHODS)}"
        f"<h3>Peak</h3>{_table(models.agencies, methods=MODEL_METHODS, peak=True)}"
        f"<h3>Sparse cells</h3>{_table(models.agencies, methods=MODEL_METHODS, sparse=True)}"
        f"<h3>Intervals, per run</h3>{_interval_rows(models)}"
        f"{agencies}"
    )


def _agency(result: AgencyResult) -> str:
    note = " — short history: fewer origins than the window allows" if result.short_history else ""
    span = f"{escape(str(result.first_day))} – {escape(str(result.last_day))}"
    return (
        f"<h3>Agency {result.agency_id}</h3>"
        f"<p class='muted'>{span}, {result.days_of_data} days, {len(result.origins)} origins{note}</p>"
        f"{_table([result])}"
    )


_STYLE = """
:root { color-scheme: light dark; --fg: #0f1a2a; --muted: #5b687d; --bg: #f1f4f7; --line: #d6dde6;
        --s0: #2750c2; --s1: #a74d21; --s2: #207454; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6ecf4; --muted: #95a1b3; --bg: #0c1219; --line: #283444;
          --s0: #86a2ff; --s1: #e08a61; --s2: #31b483; }
}
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
       font: 15px/1.6 "BIZ UDPGothic", "Hiragino Sans", system-ui, sans-serif; }
main { max-width: 860px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 24px; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--line); }
th:first-child { text-align: left; }
.muted { color: var(--muted); }
svg { width: 100%; height: auto; }
svg text { fill: var(--muted); font-size: 12px; }
.axis { stroke: var(--line); }
polyline { fill: none; stroke-width: 2; }
.s0 { stroke: var(--s0); } text.s0 { fill: var(--s0); }
.s1 { stroke: var(--s1); } text.s1 { fill: var(--s1); }
.s2 { stroke: var(--s2); } text.s2 { fill: var(--s2); }
"""

_INTRO = (
    "Forecasts made on day T from data through T−1, scored on T+1..T+7. Errors are in minutes, "
    "weighted by runs. Skill against B0 is measured only on the cells both predict."
)


def render(results: Sequence[AgencyResult], generated: date, models: ModelBacktestResult | None = None) -> str:
    agencies = "".join(_agency(r) for r in results)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Delay baselines</title><style>{_STYLE}</style></head><body><main>"
        f"<h1>Delay baselines</h1><p class='muted'>Generated {escape(str(generated))}. {_INTRO}</p>"
        f"<h2>All agencies</h2>{_table(results)}"
        f"{_model_section(models) if models else ''}"
        f"<h2>By days ahead</h2>{_horizon_chart(results)}"
        f"<h2>Peak (7–9, 17–19)</h2>{_table(results, peak=True)}"
        f"<h2>Off-peak</h2>{_table(results, peak=False)}"
        f"<h2>Sparse cells (≤ {SPARSE_RUNS} runs)</h2>{_table(results, sparse=True)}"
        f"<h2>Per agency</h2>{agencies}</main></body></html>"
    )
