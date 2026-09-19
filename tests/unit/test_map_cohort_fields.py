"""Offline tests for ``api.routers.map._cohort_fields``.

Pure function — no Postgres, no ClickHouse — lives under ``tests/unit`` per
CLAUDE.md's "Put pure logic tests under tests/unit/" convention (see
``tests/unit/test_ask_eval_numeric_helper.py`` for the same rationale).

Covers the case where a stop_sequence has zero delay samples (``route_avg_sec
is None``, from ``route_stop_profile``'s ``_round_half_up_int(...) if
a["delays"] else None`` conditional) while its cohort still qualifies for the
outlier comparison — that used to crash with ``TypeError: '>' not supported
between instances of 'NoneType' and 'float'`` instead of just reporting "not
an outlier".
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
