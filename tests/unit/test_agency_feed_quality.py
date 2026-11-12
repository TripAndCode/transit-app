"""The agency drawer's feed-quality months: newest first, as JSON-ready values,
each with its grade, which no endpoint outside the admin API carries."""

from collections.abc import Iterator, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from starlette.routing import BaseRoute

from api import agency_diagnostics as ad
from api.security import require_admin


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
        "message_age_stale_polls": None,
        "message_age_stale_pct": None,
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


def test_each_month_carries_its_grade_from_its_own_figures():
    rows = [
        _month(
            date(2026, 10, 1),
            rt_completeness_pct=Decimal("98.6"),
            message_age_polls=4,
            message_age_stale_polls=1,
            message_age_stale_pct=Decimal("25.00"),
            message_age_p50_sec=20.0,
            message_age_p95_sec=36.0,
            message_age_missing=None,
        ),
        _month(date(2026, 9, 1)),
    ]
    october, september = ad.build_feed_quality(rows)
    assert october["message_age_stale_pct"] == 25.0 and isinstance(october["message_age_stale_pct"], float)
    assert (october["grade"], october["grade_failing"], october["grade_missing"]) == ("B", ["stale_polls"], [])
    assert september["grade"] is None
    assert september["grade_failing"] == ["rt_completeness"]
    assert september["grade_missing"] == [{"criterion": "stale_polls", "reason": "archive_stamped"}]


def _has_grade(schema: Any) -> bool:
    if isinstance(schema, dict):
        if "grade" in schema.get("properties", {}):
            return True
        return any(_has_grade(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_has_grade(value) for value in schema)
    return False


def _routes(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """Every endpoint, through the wrapper newer FastAPI puts around each
    included router."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _routes(route.original_router.routes)


def _requires_admin(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is require_admin:
            return True
        pending.extend(dependency.dependencies)
    return False


def test_only_the_admin_api_returns_a_grade():
    """The grade is shown to admins only until operators have had notice and
    a right of reply (docs/methodology.md, "Publication process")."""
    from api.main import app

    graded = [
        route
        for route in _routes(app.routes)
        if route.response_model is not None and _has_grade(TypeAdapter(route.response_model).json_schema())
    ]
    assert [route.path for route in graded] == ["/api/admin/agencies/{agency_id}/diagnostics"]
    assert all(_requires_admin(route) for route in graded)
