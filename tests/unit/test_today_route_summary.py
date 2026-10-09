from datetime import datetime

from api.routers.map import build_today_routes


def _today(route, svc, sum_delay, samples, worst=600, trips=10):
    return {
        "route_code": route,
        "service_key": svc,
        "sum_delay_sec": sum_delay,
        "samples": samples,
        "worst_delay_sec": worst,
        "trips_observed": trips,
        "last_seen_at": datetime(2026, 10, 2, 3, 0),
    }


def test_today_average_rounds_half_away_from_zero_like_the_aggregate_will():
    [r] = build_today_routes([_today("R1", "平日", 5, 2)], {})
    assert r["avg_delay_sec"] == 3


def test_null_service_maps_back_to_none_and_has_no_baseline_without_one():
    [r] = build_today_routes([_today("R1", "", 600, 40)], {})
    assert (r["service_type"], r["bucket"], r["has_baseline"]) == (None, "no_baseline", False)


def test_the_closed_day_baseline_drives_bucket_and_deviation():
    base = {
        ("R1", "平日"): {"baseline_avg_min": 2.0, "baseline_p90_min": 6.0, "baseline_samples": 500, "late5_pct": 12.5}
    }
    [r] = build_today_routes([_today("R1", "平日", 420 * 40, 40)], base)
    assert (r["bucket"], r["deviation_sec"], r["baseline_avg_sec"], r["baseline_p90_sec"]) == ("anomaly", 300, 120, 360)
    assert (r["baseline_samples"], r["late5_pct"]) == (500, 12.5)


def test_worst_route_first():
    rows = build_today_routes([_today("A", "平日", 60, 1, worst=60), _today("B", "平日", 60, 1, worst=900)], {})
    assert [r["route_code"] for r in rows] == ["B", "A"]


def test_last_seen_at_carries_utc():
    [r] = build_today_routes([_today("R1", "平日", 60, 1)], {})
    assert r["last_seen_at"] == "2026-10-02T03:00:00+00:00"
