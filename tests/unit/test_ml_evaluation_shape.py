"""Shaping a stored evaluation summary into the API's fields: only the fields
the page reads, the slot interval preferred when present, names and live
flags joined in, and nothing that a newer or older summary lacks crashes."""

from datetime import date

from pipeline.reports.ml_evaluation import shape_history, shape_latest

_SUMMARY = {
    "schema": 1,
    "run_date": "2026-10-25",
    "adopted": False,
    "failures": [{"code": "agency_worse", "value": None, "agencies": [12]}],
    "headline": {"skill": 0.046, "coverage": 0.8},
    "thresholds": {"min_skill": 0.05, "coverage_low": 0.75, "coverage_high": 0.85, "min_model_days": 56},
    "point_metric": "q50",
    "params": {"window_days": 28, "point": "q50"},
    "interval": {"LGBM": {"coverage": 0.8, "mean_width": 3.0}, "LGBM slot": {"coverage": 0.78, "mean_width": 2.9}},
    "origin_skill": {"count": 28, "min": -0.3, "median": 0.05, "max": 0.18},
    "agencies": [
        {"agency_id": 8, "model_status": "model", "skill": 0.07, "lgbm_mae": 1.0, "b0_mae": 1.1, "days_of_data": 140},
        {
            "agency_id": 12,
            "model_status": "partial",
            "skill": -0.02,
            "lgbm_mae": 1.3,
            "b0_mae": 1.27,
            "days_of_data": 60,
        },
        {"agency_id": 54, "model_status": "b0", "skill": None, "lgbm_mae": 1.2, "b0_mae": 1.2, "days_of_data": 20},
    ],
    "serving_model": {"version": "2026-10-17", "window_days": 28},
}


def test_the_latest_week_keeps_the_fields_the_page_reads():
    latest = shape_latest(_SUMMARY, date(2026, 10, 25), {8: "広島電鉄", 12: "備北交通"}, {8})
    assert latest["run_date"] == date(2026, 10, 25)
    assert latest["adopted"] is False
    assert latest["failures"] == [{"code": "agency_worse", "value": None, "agencies": [12]}]
    assert (latest["skill"], latest["coverage"]) == (0.046, 0.8)
    assert latest["interval_coverage"] == 0.78  # the slot interval predict publishes, when present
    assert latest["point_metric"] == "q50"
    assert latest["serving_model_version"] == "2026-10-17"
    assert latest["thresholds"]["min_model_days"] == 56
    assert "params" not in latest and "interval" not in latest


def test_each_agency_carries_its_name_status_and_whether_it_is_live():
    rows = {a["agency_id"]: a for a in shape_latest(_SUMMARY, date(2026, 10, 25), {8: "広島電鉄"}, {8})["agencies"]}
    assert rows[8] == {
        "agency_id": 8,
        "agency_name": "広島電鉄",
        "model_status": "model",
        "skill": 0.07,
        "lgbm_mae": 1.0,
        "b0_mae": 1.1,
        "days_of_data": 140,
        "forecast_live": True,
    }
    assert rows[12]["agency_name"] is None and rows[12]["forecast_live"] is False
    assert rows[54]["skill"] is None and rows[54]["model_status"] == "b0"


def test_a_summary_without_a_slot_interval_or_serving_model_still_shapes():
    older = {**_SUMMARY, "interval": {"LGBM": {"coverage": 0.81, "mean_width": 3.0}}, "serving_model": None}
    latest = shape_latest(older, date(2026, 10, 18), {}, set())
    assert latest["interval_coverage"] == 0.81
    assert latest["serving_model_version"] is None


def test_history_is_newest_first_with_each_weeks_metric():
    rows = [
        (date(2026, 10, 18), True, 0.063, "mean"),
        (date(2026, 11, 1), True, 0.10, "q50"),
        (date(2026, 10, 25), True, 0.051, "mean"),
    ]
    assert shape_history(rows) == [
        {"run_date": date(2026, 11, 1), "adopted": True, "overall_skill": 0.10, "point_metric": "q50"},
        {"run_date": date(2026, 10, 25), "adopted": True, "overall_skill": 0.051, "point_metric": "mean"},
        {"run_date": date(2026, 10, 18), "adopted": True, "overall_skill": 0.063, "point_metric": "mean"},
    ]


class _FailingConn:
    async def fetch(self, *args):
        raise RuntimeError("relation does not exist")


async def test_the_live_agency_lookup_degrades_to_none_rather_than_failing():
    from api.routers.reports import fetch_live_ml_agencies

    assert await fetch_live_ml_agencies(_FailingConn()) == set()


def test_an_explicit_null_point_metric_reads_as_the_mean_like_the_history_does():
    latest = shape_latest({**_SUMMARY, "point_metric": None}, date(2026, 10, 25), {}, set())
    assert latest["point_metric"] == "mean"
