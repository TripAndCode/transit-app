"""Step-timing instrumentation in pipeline.analyze.

Unit-level properties only. That every aggregate actually produces a label is
a property of `analyze()` as a whole, so it is asserted against a real run in
tests/pipeline/test_analyze.py instead of being restated here.
"""

import logging

import pytest

from pipeline import analyze as analyze_mod


@pytest.fixture(autouse=True)
def _clean_registry():
    analyze_mod._step_ms.clear()
    yield
    analyze_mod._step_ms.clear()


def test_step_accumulates_repeated_labels():
    # Two phases of one aggregate's build share a label on purpose, so the
    # summary reports the aggregate once rather than splitting its cost.
    for _ in range(3):
        with analyze_mod._step("agg_x: build"):
            pass

    assert list(analyze_mod._step_ms) == ["agg_x: build"]
    assert analyze_mod._step_ms["agg_x: build"] >= 0


def test_step_records_even_when_the_body_raises():
    with pytest.raises(RuntimeError), analyze_mod._step("purge: DELETE prior rows"):
        raise RuntimeError("boom")

    assert "purge: DELETE prior rows" in analyze_mod._step_ms


def test_summary_lists_steps_slowest_first_with_shares(caplog):
    analyze_mod._step_ms.update({"fast": 10.0, "slowest": 60.0, "middle": 30.0})

    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze_mod._log_step_summary(443, wall_ms=100.0)

    lines = [r.message for r in caplog.records]
    assert "agency 443" in lines[0]
    assert [ln.split()[-1] for ln in lines[1:]] == ["slowest", "middle", "fast"]
    assert "60.0%" in lines[1]


def test_summary_reports_coverage_against_wall_clock(caplog):
    """Shares are of wall time, not of the measured subtotal.

    An unmeasured step must show up as missing coverage; dividing by the
    measured subtotal would instead inflate every instrumented step to fill
    100% and hide the gap — which is exactly how an uninstrumented aggregate
    would go unnoticed.
    """
    analyze_mod._step_ms.update({"measured": 40.0})

    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze_mod._log_step_summary(443, wall_ms=100.0)

    header, step_line = (r.message for r in caplog.records)
    assert "0.1s wall" in header
    assert "0.0s measured (40% covered)" in header
    assert "40.0%" in step_line


def test_summary_is_silent_with_nothing_recorded(caplog):
    # A run that fails before the first step should not log a 0-of-0 header.
    with caplog.at_level(logging.INFO, logger="pipeline.analyze"):
        analyze_mod._log_step_summary(443, wall_ms=0.0)

    assert caplog.records == []
