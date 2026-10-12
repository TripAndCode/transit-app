"""Merging slice rows into the figures the report and the adoption verdict read."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ml.backtest import AgencyResult, SliceRow
from ml.metrics import ErrorStats


@dataclass(frozen=True)
class Summary:
    mae: float | None
    rmse: float | None
    coverage: float | None
    skill_vs_b0: float | None


def _selected(row: SliceRow, method: str, horizon: int | None, peak: bool | None, sparse: bool | None) -> bool:
    return (
        row.method == method
        and (horizon is None or row.horizon == horizon)
        and (peak is None or row.peak == peak)
        and (sparse is None or row.sparse == sparse)
    )


def summarize(
    results: Sequence[AgencyResult],
    method: str,
    *,
    horizon: int | None = None,
    peak: bool | None = None,
    sparse: bool | None = None,
    agency_id: int | None = None,
) -> Summary:
    errors, paired, paired_b0 = ErrorStats(), ErrorStats(), ErrorStats()
    target = predicted = 0
    for result in results:
        if agency_id is not None and result.agency_id != agency_id:
            continue
        for row in result.rows:
            if not _selected(row, method, horizon, peak, sparse):
                continue
            errors.merge(row.errors)
            paired.merge(row.paired)
            paired_b0.merge(row.paired_b0)
            target += row.target_runs
            predicted += row.predicted_runs
    skill = None
    if paired.mae is not None and paired_b0.mae is not None:
        # B0 exact on every shared cell: no room to improve, so no skill either way.
        skill = 1 - paired.mae / paired_b0.mae if paired_b0.mae else 0.0
    return Summary(errors.mae, errors.rmse, predicted / target if target else None, skill)
