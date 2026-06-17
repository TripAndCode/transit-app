"""DB-free unit tests for the pure _aggregate_by_route helper.

The helper collapses per-(route, service_type) day rows to one weighted entry
per route_code so a digest never emits duplicate-route_code movers. Baselines
are looked up separately (route-grain) in build_digest, not here.
"""

from pipeline.digest.build import _aggregate_by_route


def test_single_route_single_service_type_passthrough():
    # Row tuple: (route_code, avg_delay_sec, samples)
    rows = [("44372", 480, 50)]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e == {
        "route_code": "44372",
        "avg_delay_sec": 480,
        "samples": 50,
    }


def test_two_service_types_collapse_to_one_weighted_entry():
    rows = [
        ("44372", 480, 50),
        ("44372", 600, 30),
    ]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e["route_code"] == "44372"
    assert e["samples"] == 80
    # round((480*50 + 600*30) / 80) = round(525) = 525
    assert e["avg_delay_sec"] == 525


def test_multiple_routes_preserve_order():
    rows = [
        ("44372", 480, 50),
        ("12", 120, 40),
    ]
    out = _aggregate_by_route(rows)
    assert [e["route_code"] for e in out] == ["44372", "12"]
    assert out[1]["avg_delay_sec"] == 120
    assert out[1]["samples"] == 40
