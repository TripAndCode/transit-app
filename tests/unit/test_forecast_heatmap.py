"""Pure unit tests for the day×hour heatmap summarizer (DB-free)."""

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
