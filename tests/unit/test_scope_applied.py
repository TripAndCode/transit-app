import pytest

from api.routers.reports import _REPORT_TYPES, report_scope_applied
from api.scope_applied import SCOPE_FIELDS, scope_applied


def test_scope_applied_names_every_field():
    applied = scope_applied("from", "to")
    assert list(applied) == list(SCOPE_FIELDS)
    assert applied["from"] and applied["to"]
    assert not any(applied[f] for f in SCOPE_FIELDS if f not in ("from", "to"))


def test_scope_applied_rejects_a_typo():
    with pytest.raises(ValueError):
        scope_applied("form")


def test_every_report_type_declares_its_scope():
    for report_type in _REPORT_TYPES:
        applied = report_scope_applied(report_type)
        assert set(applied) == set(SCOPE_FIELDS)
        assert applied["hour"] is False  # no aggregate serves hour yet
        assert applied["stop"] is False and applied["dir"] is False


def test_tolerances_are_applied_only_where_the_report_has_them():
    assert report_scope_applied("on_time")["late"] and report_scope_applied("on_time")["early"]
    assert report_scope_applied("worst_5min")["late"] and not report_scope_applied("worst_5min")["early"]
    assert not report_scope_applied("ranking")["late"]


def test_reports_that_override_a_field_say_so():
    assert not report_scope_applied("compare_ranking")["dow"]
    assert not report_scope_applied("compare_ranking")["service"]
    assert not report_scope_applied("dow_weekday")["dow"]
    assert not report_scope_applied("dwell_run")["time_band"]
    assert report_scope_applied("ranking")["service"]
