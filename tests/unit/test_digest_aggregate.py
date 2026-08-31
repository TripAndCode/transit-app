"""DB-free unit tests for the pure _aggregate_by_route helper.

The helper collapses per-(route, service_type) day rows to one weighted entry
per route_code so a digest never emits duplicate-route_code movers. Baselines
are looked up separately (route-grain) in build_digest, not here.
"""

from pipeline.digest.build import _aggregate_by_route


def test_single_route_single_service_type_passthrough():
    # Row tuple: (route_code, samples, sum_delay_sec)
    rows = [("44372", 50, 480 * 50)]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    assert e == {
        "route_code": "44372",
        "avg_delay_sec": 480,
        "samples": 50,
        "sum_delay_sec": 24000,
    }


def test_two_service_types_collapse_to_one_weighted_entry():
    rows = [
        ("44372", 50, 480 * 50),
        ("44372", 30, 600 * 30),
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
        ("44372", 50, 480 * 50),
        ("12", 40, 120 * 40),
    ]
    out = _aggregate_by_route(rows)
    assert [e["route_code"] for e in out] == ["44372", "12"]
    assert out[1]["avg_delay_sec"] == 120
    assert out[1]["samples"] == 40


def test_pools_exact_sum_delay_sec_not_rounded_avg_delay_sec():
    """Proves the fix: pooling from the raw sum_delay_sec differs from
    re-weighting each service_type's already-rounded avg_delay_sec whenever
    that per-row average isn't itself exactly representable in whole seconds.

    Two service_types: 7 samples averaging 100.4s (stored/rounded as 100) and
    3 samples averaging 100.9s (stored/rounded as 101). The exact route-level
    mean is (100.4*7 + 100.9*3) / 10 = 100.55s -> rounds to 101.
    Re-weighting the ALREADY-ROUNDED per-row averages instead would give
    round((100*7 + 101*3) / 10) = round(100.3) = 100 -- a different, wrong
    answer purely from the intermediate rounding, which is exactly the bug
    this helper's sum_delay_sec-based pooling avoids.
    """
    rows = [
        ("44372", 7, 703),  # exact sum: 100.4 * 7 = 702.8 -> 703 (nearest int)
        ("44372", 3, 303),  # exact sum: 100.9 * 3 = 302.7 -> 303 (nearest int)
    ]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    # (703 + 303) / 10 = 100.6 -> round-half-to-even in Python's round() gives 101
    # (100.6 rounds unambiguously to 101 either way -- no half-boundary tie here).
    assert out[0]["avg_delay_sec"] == 101
    # The old (buggy) re-weight-the-rounded-average path would have computed
    # round((100*7 + 101*3) / 10) == 100 instead -- demonstrating the two
    # methods diverge on the same input.
    old_buggy_result = round((100 * 7 + 101 * 3) / 10)
    assert old_buggy_result == 100
    assert out[0]["avg_delay_sec"] != old_buggy_result


def test_null_sum_delay_sec_row_is_skipped_not_crashed_on():
    """sum_delay_sec is nullable (migration 0028) -- any agg_route_daily row
    analyze() hasn't rewritten since that migration can have samples set but
    sum_delay_sec still None. A naive unconditional ``+=`` would raise
    TypeError (int += NoneType); this helper must instead skip such a row
    entirely (excluded from both the numerator and the denominator, not just
    the numerator) so the surviving route-level entry is undistorted.
    """
    rows = [
        ("44372", 50, None),  # not yet rewritten by analyze() -- must not crash
        ("44372", 50, 480 * 50),
    ]
    out = _aggregate_by_route(rows)
    assert len(out) == 1
    e = out[0]
    # The None row's samples must NOT be folded in alongside the populated
    # row's -- only the populated row contributes.
    assert e["samples"] == 50
    assert e["avg_delay_sec"] == 480
    assert e["sum_delay_sec"] == 24000


def test_all_rows_null_sum_delay_sec_yields_no_entry():
    """A route with every service_type row still unbackfilled must vanish
    from the output entirely, not appear with a misleading zeroed average."""
    rows = [("44372", 50, None)]
    out = _aggregate_by_route(rows)
    assert out == []
