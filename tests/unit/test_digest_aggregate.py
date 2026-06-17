"""DB-free unit tests for the pure _aggregate_by_route helper.

The helper collapses per-(route, service_type) day rows to one weighted entry
per route_code so a digest never emits duplicate-route_code movers.
"""

from pipeline.digest.build import _aggregate_by_route


def test_single_route_single_service_type_passthrough():
    # Row tuple: (route_code, avg_delay_sec, samples, baseline_avg_min, baseline_p90_min)
    rows = [("44372", 480, 50, 3.0, 5.0)]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e == {
        "route_code": "44372",
        "avg_delay_sec": 480,
        "samples": 50,
        "baseline_avg_sec": 180,  # round(3.0 * 60)
        "baseline_p90_sec": 300,  # round(5.0 * 60)
    }


def test_two_service_types_collapse_to_one_weighted_entry():
    rows = [
        ("44372", 480, 50, 3.0, 5.0),
        ("44372", 600, 30, 3.0, 5.0),
    ]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e["route_code"] == "44372"
    assert e["samples"] == 80
    # round((480*50 + 600*30) / 80) = round(525) = 525
    assert e["avg_delay_sec"] == 525
    # baseline weighted by today's samples; both rows have the same baseline.
    assert e["baseline_avg_sec"] == 180
    assert e["baseline_p90_sec"] == 300


def test_route_without_baseline_yields_none_baselines():
    rows = [("44372", 480, 50, None, None)]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e["avg_delay_sec"] == 480
    assert e["samples"] == 50
    assert e["baseline_avg_sec"] is None
    assert e["baseline_p90_sec"] is None
