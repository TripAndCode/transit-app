"""The agency drawer's feed-quality months: newest first, as JSON-ready values."""

from datetime import date
from decimal import Decimal

from api import agency_diagnostics as ad


def _month(month: date, **over):
    row = {
        "month": month,
        "through_date": date(month.year, month.month, 28),
        "covered_days": 28,
        "window_days": 28,
        "observed_trips": 693,
        "planned_trips": 1000,
        "rt_completeness_pct": Decimal("69.3"),
        "rt_completeness_missing": None,
        "observed_stop_events": None,
        "planned_stop_events": None,
        "stop_plan_days": 0,
        "stop_completeness_pct": None,
        "stop_completeness_missing": "no_stop_plan",
        "message_age_polls": 0,
        "message_age_undated_polls": 0,
        "message_age_p50_sec": None,
        "message_age_p95_sec": None,
        "message_age_missing": "archive_stamped",
        "raw_samples": 1000,
        "clamp_count": 3,
        "clamp_pct": Decimal("0.30"),
        "clamp_missing": None,
        "stop_time_updates": 1200,
        "stop_id_pct": Decimal("100.0"),
        "arr_delay_pct": Decimal("7.2"),
        "schedule_relationship_trip_pct": Decimal("100.0"),
        "schedule_relationship_stop_pct": Decimal("100.0"),
        "field_coverage_missing": None,
        "vehicle_positions_available": None,
        "vehicle_positions_missing": "not_recorded",
    }
    return {**row, **over}


def test_the_drawer_reads_six_months():
    assert ad.FEED_QUALITY_MONTHS == 6
    assert "LIMIT $2" in ad.FEED_QUALITY_SQL and "ORDER BY month DESC" in ad.FEED_QUALITY_SQL


def test_months_keep_their_order_and_become_plain_json_values():
    rows = [
        _month(date(2026, 10, 1)),
        _month(date(2026, 9, 1), rt_completeness_pct=None, rt_completeness_missing="no_plan"),
    ]
    months = ad.build_feed_quality(rows)
    assert [m["month"] for m in months] == ["2026-10-01", "2026-09-01"]
    assert months[0]["through_date"] == "2026-10-28"
    assert months[0]["rt_completeness_pct"] == 69.3 and isinstance(months[0]["rt_completeness_pct"], float)
    assert months[0]["clamp_pct"] == 0.3
    assert (months[1]["rt_completeness_pct"], months[1]["rt_completeness_missing"]) == (None, "no_plan")
    assert months[0]["message_age_missing"] == "archive_stamped"
