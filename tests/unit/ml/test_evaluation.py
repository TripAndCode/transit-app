"""The weekly evaluation summary the web app ingests. Fixture: agency 8 is
scored on the model (+10% vs B0); agency 11 sat on B0 at every cutoff, so its
"model" rows repeat B0's exactly and its 0% must not be shown as the model's."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from ml.backtest import AgencyResult, SliceRow
from ml.evaluation import SCHEMA, build_summary
from ml.metrics import ErrorStats
from ml.model_result import IntervalStats, ModelBacktestResult

RUN = date(2026, 10, 25)
AT = datetime(2026, 10, 25, 4, 55, tzinfo=ZoneInfo("Asia/Tokyo"))


def _row(method, mae_err, paired, paired_b0):
    return SliceRow(
        method,
        1,
        False,
        False,
        target_runs=10,
        predicted_runs=10,
        errors=ErrorStats(10, mae_err, mae_err),
        paired=ErrorStats(10, paired, paired),
        paired_b0=ErrorStats(10, paired_b0, paired_b0),
    )


def _result(params=None):
    day = date(2026, 10, 24)
    eight = AgencyResult(
        8, date(2026, 6, 1), day, 140, [day], False, [_row("LGBM", 9.0, 9.0, 10.0), _row("B0", 10.0, 10.0, 10.0)]
    )
    eleven = AgencyResult(
        11, date(2026, 9, 10), day, 45, [day], True, [_row("LGBM", 12.0, 12.0, 12.0), _row("B0", 12.0, 12.0, 12.0)]
    )
    return ModelBacktestResult(
        params=params if params is not None else {"window_days": 28},
        origins=[day],
        cutoffs=[date(2026, 10, 23)],
        agencies=[eight, eleven],
        intervals={
            "LGBM": {"8": IntervalStats(10, 8, 30.0), "11": IntervalStats(10, 8, 20.0)},
            "LGBM slot": {"8": IntervalStats(10, 7, 25.0)},
        },
        origin_skill=[(day, 0.12), (date(2026, 10, 17), -0.03), (date(2026, 10, 10), None)],
        fallback_agencies=[11],
    )


def test_a_not_adopted_week_is_summarized_with_its_failure_as_data():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta=None)
    assert summary["schema"] == SCHEMA == 1
    assert summary["run_date"] == "2026-10-25"
    assert summary["generated_at"] == "2026-10-25T04:55:00+09:00"
    assert summary["adopted"] is False
    # overall paired MAE 21/20 vs B0 22/20 -> skill 1 - 1.05/1.1, just under 5%
    assert summary["failures"] == [{"code": "skill_below_min", "value": pytest.approx(1 - 1.05 / 1.1), "agencies": []}]
    assert summary["headline"] == {"skill": pytest.approx(1 - 1.05 / 1.1), "coverage": pytest.approx(0.8)}
    assert summary["thresholds"] == {
        "min_skill": 0.05,
        "coverage_low": 0.75,
        "coverage_high": 0.85,
        "min_model_days": 56,
    }


def test_an_agency_on_b0_shows_no_skill_rather_than_a_misleading_zero():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta=None)
    rows = {a["agency_id"]: a for a in summary["agencies"]}
    assert rows[8]["model_status"] == "model"
    assert rows[8]["skill"] == pytest.approx(0.1)
    assert rows[8]["lgbm_mae"] == pytest.approx(0.9)
    assert rows[8]["b0_mae"] == pytest.approx(1.0)
    assert rows[8]["interval_coverage"] == pytest.approx(0.8)
    assert rows[8]["days_of_data"] == 140
    assert rows[11]["model_status"] == "b0"
    assert rows[11]["skill"] is None
    assert rows[11]["short_history"] is True


def test_an_agency_on_b0_at_only_some_cutoffs_keeps_its_blended_skill():
    """`fallback_agencies` holds every agency on B0 at any cutoff. One that
    crossed the history threshold mid-backtest was scored on the model for
    its later weeks, so its skill is real, and a negative one is what the
    verdict names: the document must show it, not hide the reason it failed."""
    result = _result()
    day = date(2026, 10, 24)
    twelve = AgencyResult(
        12, date(2026, 8, 20), day, 66, [day], True, [_row("LGBM", 11.0, 11.0, 10.0), _row("B0", 10.0, 10.0, 10.0)]
    )
    result.agencies.append(twelve)
    result.fallback_agencies.append(12)
    summary = build_summary(result, run_date=RUN, generated_at=AT, serving_meta=None)
    rows = {a["agency_id"]: a for a in summary["agencies"]}
    assert rows[12]["model_status"] == "partial"
    assert rows[12]["skill"] == pytest.approx(-0.1)
    assert {"code": "agency_worse", "value": None, "agencies": [12]} in summary["failures"]


def test_overall_figures_and_every_interval_method_pass_through():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta=None)
    assert summary["overall"]["LGBM"]["mae"] == pytest.approx(1.05)
    assert summary["overall"]["B0"]["mae"] == pytest.approx(1.1)
    assert set(summary["interval"]) == {"LGBM", "LGBM slot"}
    assert summary["interval"]["LGBM"]["coverage"] == pytest.approx(0.8)
    assert summary["interval"]["LGBM slot"]["coverage"] == pytest.approx(0.7)
    assert summary["interval"]["LGBM slot"]["mean_width"] == pytest.approx(2.5)


def test_origin_skill_is_summarized_like_the_report_does():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta=None)
    assert summary["origin_skill"] == {"count": 2, "min": -0.03, "median": 0.12, "max": 0.12}


def test_params_and_the_serving_meta_pass_through_with_unknown_keys():
    params = {"window_days": 28, "point": "q50", "clip_quantile": 0.99}
    meta = {"version": "2026-10-24", "window_days": 28, "interval_shift": 0.04, "point": "q50"}
    summary = build_summary(_result(params), run_date=RUN, generated_at=AT, serving_meta=meta)
    assert summary["params"] == params
    assert summary["point_metric"] == "q50"
    assert summary["serving_model"] == meta


def test_point_metric_defaults_to_the_mean_for_a_model_without_one():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta=None)
    assert summary["point_metric"] == "mean"
    assert summary["serving_model"] is None


def test_the_summary_is_plain_json():
    summary = build_summary(_result(), run_date=RUN, generated_at=AT, serving_meta={"version": "x"})
    assert json.loads(json.dumps(summary)) == summary
