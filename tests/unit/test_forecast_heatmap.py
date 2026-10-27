"""Pure unit tests for the day×hour heatmap summarizer (DB-free)."""

from datetime import date

from pipeline.reports.forecast import summarize_expected_delay_heatmap


def _rows(*t):  # (dow, hour, avg_min, samples)
    return [{"dow": d, "hour": h, "avg_min": a, "samples": s} for (d, h, a, s) in t]


def test_full_grid_168():
    out = summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100)), "R1")
    assert len(out["cells"]) == 168
    c = next(x for x in out["cells"] if x["dow"] == 1 and x["hour"] == 8)
    assert c["expected_avg_min"] == 5.0
    assert c["samples"] == 100
    assert c["low_confidence"] is False
    miss = next(x for x in out["cells"] if x["dow"] == 2 and x["hour"] == 0)
    assert miss["expected_avg_min"] is None
    assert miss["samples"] == 0


def test_low_conf_boundary():
    out = summarize_expected_delay_heatmap(_rows((3, 9, 2.0, 29), (3, 10, 2.0, 30)), "R1")
    assert next(x for x in out["cells"] if x["dow"] == 3 and x["hour"] == 9)["low_confidence"] is True
    assert next(x for x in out["cells"] if x["dow"] == 3 and x["hour"] == 10)["low_confidence"] is False


def test_null_avg_rows_skipped():
    out = summarize_expected_delay_heatmap(_rows((1, 8, None, 100), (1, 9, 3.0, 0)), "R1")
    assert all(c["expected_avg_min"] is None and c["samples"] == 0 for c in out["cells"])


def test_disclaimer_both_locales():
    for loc in ("ja", "en"):
        out = summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100)), "R1", locale=loc)
        assert out["disclaimer"]
        assert "p90" not in out["disclaimer"].lower()
        assert "percentile" not in out["disclaimer"].lower()


def test_route_echoed():
    assert summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100)), "R9")["route"] == "R9"


def test_forecast_days_name_the_service_date_each_ml_weekday_row_stands_for():
    ml_cells = [
        {"dow": 4, "hour": 8, "mean_min": 3.0, "service_date": date(2026, 10, 29)},
        {"dow": 2, "hour": 8, "mean_min": 2.0, "service_date": date(2026, 10, 27)},
        {"dow": 2, "hour": 9, "mean_min": 2.5, "service_date": date(2026, 10, 27)},
    ]
    out = summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100)), "R1", ml_cells=ml_cells)
    assert out["forecast_days"] == [
        {"dow": 2, "service_date": date(2026, 10, 27)},
        {"dow": 4, "service_date": date(2026, 10, 29)},
    ]


def test_a_baseline_only_heatmap_forecasts_no_date():
    assert summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100)), "R1")["forecast_days"] == []


def test_a_forecast_cell_carries_its_range_and_a_past_average_cell_none():
    ml_cells = [{"dow": 1, "hour": 8, "mean_min": 6.4, "q10": 1.96, "q90": 11.04, "service_date": date(2026, 10, 26)}]
    out = summarize_expected_delay_heatmap(_rows((1, 8, 5.0, 100), (1, 9, 4.0, 100)), "R1", ml_cells=ml_cells)
    ml = next(c for c in out["cells"] if (c["dow"], c["hour"]) == (1, 8))
    past = next(c for c in out["cells"] if (c["dow"], c["hour"]) == (1, 9))
    assert (ml["q10"], ml["q90"]) == (2.0, 11.0)
    assert (past["source"], past["q10"], past["q90"]) == ("baseline", None, None)
