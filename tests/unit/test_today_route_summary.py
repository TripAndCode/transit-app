from datetime import datetime

import api.routers.map as map_router
from api.routers.map import MIN_SAME_CLASS_BASELINE_DAYS, build_today_routes, pool_today_baselines
from api.triage import classify_route
from tests.fixtures.route_dist import hist_of


def _today(route, sum_delay, samples, worst=600, trips=10):
    return {
        "route_code": route,
        "sum_delay_sec": sum_delay,
        "samples": samples,
        "worst_delay_sec": worst,
        "trips_observed": trips,
        "last_seen_at": datetime(2026, 10, 2, 3, 0),
    }


def _pool(route, same_class, days, delays):
    return {
        "route_code": route,
        "same_class": same_class,
        "days": days,
        "samples": len(delays),
        "sum_delay_sec": sum(delays),
        "late5_count": sum(1 for d in delays if d > 300),
        "hist": hist_of(delays),
    }


_BASE = {"baseline_avg_sec": 120, "baseline_p90_sec": 360, "baseline_samples": 500, "late5_pct": 12.5}


def test_today_average_rounds_half_away_from_zero_like_the_aggregate_will():
    [r] = build_today_routes([_today("R1", 5, 2)], {})
    assert r["avg_delay_sec"] == 3


def test_a_row_is_one_route_and_names_no_service_id():
    [r] = build_today_routes([_today("R1", 600, 40)], {})
    assert "service_type" not in r
    assert (r["route_code"], r["bucket"], r["has_baseline"]) == ("R1", "no_baseline", False)


def test_the_closed_day_baseline_drives_bucket_and_deviation():
    [r] = build_today_routes([_today("R1", 420 * 40, 40)], {"R1": _BASE})
    assert (r["bucket"], r["deviation_sec"], r["baseline_avg_sec"], r["baseline_p90_sec"]) == ("anomaly", 300, 120, 360)
    assert (r["baseline_samples"], r["late5_pct"]) == (500, 12.5)


def test_classify_route_gets_todays_pooled_figures_and_the_routes_baseline(monkeypatch):
    calls = []

    def spy(*args):
        calls.append(args)
        return classify_route(*args)

    monkeypatch.setattr(map_router, "classify_route", spy)
    build_today_routes([_today("R1", 7 * 100, 7), _today("R2", 60, 1)], {"R1": _BASE})
    assert calls == [(100, 120, 360, 7), (60, None, None, 1)]


def test_worst_route_first():
    rows = build_today_routes([_today("A", 60, 1, worst=60), _today("B", 60, 1, worst=900)], {})
    assert [r["route_code"] for r in rows] == ["B", "A"]


def test_last_seen_at_carries_utc():
    [r] = build_today_routes([_today("R1", 60, 1)], {})
    assert r["last_seen_at"] == "2026-10-02T03:00:00+00:00"


def test_enough_same_class_days_make_the_baseline_alone():
    same = [100, 200, 400]
    rows = [_pool("R1", True, MIN_SAME_CLASS_BASELINE_DAYS, same), _pool("R1", False, 20, [0] * 50)]
    assert pool_today_baselines(rows) == {
        "R1": {"baseline_avg_sec": 233, "baseline_p90_sec": 402, "baseline_samples": 3, "late5_pct": 33.3}
    }


def test_too_few_same_class_days_pool_every_class():
    rows = [_pool("R1", True, MIN_SAME_CLASS_BASELINE_DAYS - 1, [900]), _pool("R1", False, 20, [0, 60, 120])]
    assert pool_today_baselines(rows) == {
        "R1": {"baseline_avg_sec": 270, "baseline_p90_sec": 936, "baseline_samples": 4, "late5_pct": 25.0}
    }


def test_a_route_with_only_other_classes_takes_their_pool():
    assert pool_today_baselines([_pool("R1", False, 9, [60, 60])])["R1"]["baseline_avg_sec"] == 60
