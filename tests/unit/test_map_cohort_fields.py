"""Offline tests for ``api.routers.map._cohort_fields``.

Pure function — no Postgres, no ClickHouse — lives under ``tests/unit`` per
CLAUDE.md's "Put pure logic tests under tests/unit/" convention (see
``tests/unit/test_ask_eval_numeric_helper.py`` for the same rationale).

``route_avg_sec`` is ``None`` for a stop_sequence with zero delay samples
(``route_stop_profile`` yields ``_round_half_up_int(...) if a["delays"] else
None``). A stop with nothing measured is not an outlier, so ``is_outlier``
must stay ``False`` there however the cohort compares — the absent value can
never take part in the magnitude comparison.
"""

from __future__ import annotations

from api.routers.map import _cohort_fields


def test_cohort_fields_stop_with_no_samples_is_not_an_outlier() -> None:
    cohort = {
        "s1": {
            "cohort_avg_delay_sec": 100.0,
            "cohort_route_count": 2,
            "cohort_samples": 50,
        }
    }

    result = _cohort_fields("s1", None, cohort)

    assert result["is_outlier"] is False
    assert result["cohort_avg_delay_sec"] == 100.0
    assert result["cohort_route_count"] == 2


def test_cohort_fields_stop_with_samples_still_detects_outlier() -> None:
    cohort = {
        "s1": {
            "cohort_avg_delay_sec": 100.0,
            "cohort_route_count": 2,
            "cohort_samples": 50,
        }
    }

    result = _cohort_fields("s1", 200, cohort)

    assert result["is_outlier"] is True
