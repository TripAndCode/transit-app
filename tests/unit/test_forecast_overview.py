import pytest

from pipeline.reports.forecast import BANDS, band_of, hourly_cells_to_dow_band, summarize_agency_overview


def test_band_of_boundaries():
    assert band_of(0) == "early"
    assert band_of(5) == "early"
    assert band_of(6) == "morning"
    assert band_of(9) == "midday"
    assert band_of(16) == "evening"
    assert band_of(19) == "night"
    assert band_of(23) == "night"


def test_grid_always_35_cells_and_filled_for_empty_input():
    out = summarize_agency_overview([], [])
    assert len(out["grid"]) == 7 * len(BANDS) == 35
    assert all(c["expected_avg_min"] is None and c["samples"] == 0 for c in out["grid"])
    assert out["worst"] is None
    assert out["routes"] == []
    assert out["disclaimer"]


def test_band_pools_hours_sample_weighted():
    rows = [
        {"dow": 1, "hour": 9, "avg_min": 2.0, "samples": 10},
        {"dow": 1, "hour": 12, "avg_min": 8.0, "samples": 40},
    ]
    out = summarize_agency_overview(rows, [])
    cell = next(c for c in out["grid"] if c["dow"] == 1 and c["band"] == "midday")
    assert cell["expected_avg_min"] == pytest.approx((2 * 10 + 8 * 40) / 50, abs=0.05)
    assert cell["samples"] == 50


def test_worst_excludes_low_confidence():
    rows = [
        {"dow": 2, "hour": 17, "avg_min": 99.0, "samples": 5},  # huge but low-conf (<30)
        {"dow": 3, "hour": 12, "avg_min": 6.0, "samples": 200},  # real worst
    ]
    out = summarize_agency_overview(rows, [])
    assert out["worst"]["dow"] == 3
    assert out["worst"]["band"] == "midday"
    assert out["worst"]["expected_avg_min"] == pytest.approx(6.0, abs=0.05)


def test_routes_ranked_desc_low_conf_last_and_capped():
    route_rows = [
        {"route_code": "A", "route_name": "Alpha", "avg_min": 3.0, "samples": 100},
        {"route_code": "B", "route_name": "Bravo", "avg_min": 9.0, "samples": 100},
        {"route_code": "C", "route_name": "Charlie", "avg_min": 50.0, "samples": 4},  # low-conf
    ]
    out = summarize_agency_overview([], route_rows, top_n=2)
    assert [r["route_code"] for r in out["routes"]] == ["B", "A"]  # C sorted last, dropped by top_n
    assert out["routes"][0]["low_confidence"] is False


def test_hourly_cells_to_dow_band_pools_by_derived_dow():
    # 2026-05-19 is a Tuesday (dow=2), 2026-05-20 is a Wednesday (dow=3).
    hourly = [
        {"date": "2026-05-19", "hour": 9, "avg_min": 2.0, "samples": 10},
        {"date": "2026-05-19", "hour": 12, "avg_min": 8.0, "samples": 40},
        {"date": "2026-05-20", "hour": 17, "avg_min": 5.0, "samples": 60},
    ]
    out = hourly_cells_to_dow_band(hourly)
    tue_midday = next(c for c in out["grid"] if c["dow"] == 2 and c["band"] == "midday")
    assert tue_midday["expected_avg_min"] == pytest.approx((2 * 10 + 8 * 40) / 50, abs=0.05)
    assert tue_midday["samples"] == 50
    wed_evening = next(c for c in out["grid"] if c["dow"] == 3 and c["band"] == "evening")
    assert wed_evening["expected_avg_min"] == pytest.approx(5.0, abs=0.05)


def test_hourly_cells_to_dow_band_no_routes_or_disclaimer_keys():
    out = hourly_cells_to_dow_band([])
    assert set(out.keys()) == {"grid", "worst"}
    assert len(out["grid"]) == 35
    assert out["worst"] is None


def test_hourly_cells_to_dow_band_worst_window():
    hourly = [
        {"date": "2026-05-22", "hour": 17, "avg_min": 9.0, "samples": 200},  # Fri (dow=5) evening
        {"date": "2026-05-19", "hour": 9, "avg_min": 1.0, "samples": 200},  # Tue morning
    ]
    out = hourly_cells_to_dow_band(hourly)
    assert out["worst"]["dow"] == 5
    assert out["worst"]["band"] == "evening"
    assert out["worst"]["expected_avg_min"] == pytest.approx(9.0, abs=0.05)


def test_routes_carry_recent_daily_trend_oldest_first():
    route_rows = [
        {"route_code": "A", "route_name": "Alpha", "avg_min": 5.0, "samples": 100},
    ]
    recent_daily_rows = [
        {"date": "2026-06-01", "route_code": "A", "avg_min": 2.0},
        {"date": "2026-06-02", "route_code": "A", "avg_min": 4.0},
        {"date": "2026-06-03", "route_code": "A", "avg_min": 6.0},
    ]
    out = summarize_agency_overview([], route_rows, recent_daily_rows)
    assert out["routes"][0]["recent_daily"] == [2.0, 4.0, 6.0]


def test_routes_default_to_empty_recent_daily_when_absent():
    route_rows = [
        {"route_code": "A", "route_name": "Alpha", "avg_min": 5.0, "samples": 100},
    ]
    out = summarize_agency_overview([], route_rows)
    assert out["routes"][0]["recent_daily"] == []


def test_routes_skip_null_avg_min_in_recent_daily():
    # NULLIF(SUM(samples), 0) in the SQL can legitimately produce a NULL
    # avg_min for a (date, route_code) with zero total samples — this must
    # be dropped, not appended as a None/gap in the sparkline's point list.
    route_rows = [
        {"route_code": "A", "route_name": "Alpha", "avg_min": 5.0, "samples": 100},
    ]
    recent_daily_rows = [
        {"date": "2026-06-01", "route_code": "A", "avg_min": 2.0},
        {"date": "2026-06-02", "route_code": "A", "avg_min": None},
        {"date": "2026-06-03", "route_code": "A", "avg_min": 6.0},
    ]
    out = summarize_agency_overview([], route_rows, recent_daily_rows)
    assert out["routes"][0]["recent_daily"] == [2.0, 6.0]


def test_routes_sort_recent_daily_by_date_even_if_rows_arrive_unsorted():
    # summarize_agency_overview must not trust the caller's row order for
    # "oldest first" — it sorts by date itself.
    route_rows = [
        {"route_code": "A", "route_name": "Alpha", "avg_min": 5.0, "samples": 100},
    ]
    recent_daily_rows = [
        {"date": "2026-06-03", "route_code": "A", "avg_min": 6.0},
        {"date": "2026-06-01", "route_code": "A", "avg_min": 2.0},
        {"date": "2026-06-02", "route_code": "A", "avg_min": 4.0},
    ]
    out = summarize_agency_overview([], route_rows, recent_daily_rows)
    assert out["routes"][0]["recent_daily"] == [2.0, 4.0, 6.0]
