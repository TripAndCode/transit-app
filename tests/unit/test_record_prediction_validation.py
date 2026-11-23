"""scripts/record_prediction_validation.py: the row a validation report records."""

import re
from datetime import date

import pytest

from scripts import record_prediction_validation as record
from scripts import validate_vp_predictions as vv


def _summary(**over):
    summary = {
        "schema": vv.SCHEMA,
        "agency_id": 8,
        "from": "2026-11-13",
        "to": "2026-11-21",
        "thresholds": {"on_time_early_tolerance_sec": 60, "on_time_late_tolerance_sec": 300},
        "coverage": {"stop_events": 1000, "with_passage": 850},
        "figures": {
            "on_time": {
                "n": 850,
                "kept_to_observed": {
                    "early": {"early": 10, "on_time": 5, "late": 0},
                    "on_time": {"early": 30, "on_time": 600, "late": 40},
                    "late": {"early": 0, "on_time": 15, "late": 150},
                },
            }
        },
        "days": [{"missing_archives": []}, {"missing_archives": []}],
    }
    return {**summary, **over}


def test_a_report_records_its_period_window_and_on_time_counts():
    assert record.validation(_summary()) == record.Validation(
        agency_id=8,
        from_date=date(2026, 11, 13),
        to_date=date(2026, 11, 21),
        report_schema=vv.SCHEMA,
        early_tolerance_sec=60,
        late_tolerance_sec=300,
        stop_events=1000,
        compared=850,
        kept_on_time=670,
        observed_on_time=620,
    )


def test_on_time_by_kept_delay_counts_a_row_and_by_observed_delay_a_column():
    """A bias moves events into on time from one side as it moves them out on
    the other, so the two counts are the kept class's row and the observed
    class's column, not the events both call on time."""
    row = record.validation(_summary())
    assert (row.kept_on_time, row.observed_on_time) == (30 + 600 + 40, 5 + 600 + 15)


def test_the_description_gives_both_rates_and_their_difference():
    assert record.describe(record.validation(_summary())) == (
        "agency 8 2026-11-13..2026-11-21: 850/1000 stop events compared, on time 78.8% kept vs 72.9% observed (+5.9 pt)"
    )


def test_a_window_open_on_the_early_side_is_kept_as_none():
    summary = _summary(thresholds={"on_time_early_tolerance_sec": None, "on_time_late_tolerance_sec": 300})
    assert record.validation(summary).early_tolerance_sec is None


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"schema": vv.SCHEMA - 1}, f"made under schema {vv.SCHEMA - 1}"),
        ({"days": [{"missing_archives": ["vp/8/2026/11/13.tar"]}]}, "1 archive(s) missing"),
        ({"figures": {"on_time": {"n": 0, "kept_to_observed": {}}}}, "no stop event had an observed passage"),
    ],
)
def test_a_report_that_cannot_stand_for_its_range_is_refused(over, why):
    with pytest.raises(ValueError, match=re.escape(why)):
        record.validation(_summary(**over))
