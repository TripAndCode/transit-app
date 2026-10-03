from datetime import date

import pytest
from fastapi import HTTPException

from api.range import RangeCtx, canonical_dow, clamp_range_ctx, dow_clause, dow_clause_ch, dow_isodays
from pipeline.reports.overview import _dow_matches


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("all", "all"),
        ("weekday", "weekday"),
        ("weekend", "weekend"),
        ("mon", "mon"),
        ("wed,mon", "mon,wed"),
        ("mon,tue,wed,thu,fri", "weekday"),
        ("sun,sat", "weekend"),
        ("mon,tue,wed,thu,fri,sat,sun", "all"),
        ("mon,mon,tue", "mon,tue"),
    ],
)
def test_canonical_dow_orders_dedupes_and_folds_legacy_groups(raw, expected):
    assert canonical_dow(raw) == expected


@pytest.mark.parametrize("raw", ["tuesday", "Mon", "mon,,", "", ",", "mon;tue", 3, None])
def test_canonical_dow_rejects_anything_else_with_422(raw):
    with pytest.raises(HTTPException) as exc:
        canonical_dow(raw)
    assert exc.value.status_code == 422


def test_dow_isodays():
    assert dow_isodays("all") is None
    assert dow_isodays("weekday") == frozenset({1, 2, 3, 4, 5})
    assert dow_isodays("weekend") == frozenset({6, 7})
    assert dow_isodays("mon,wed") == frozenset({1, 3})


def _ctx(dow: str) -> RangeCtx:
    return RangeCtx(from_date=date(2026, 9, 1), to_date=date(2026, 9, 7), dow=dow)


def test_sql_and_clickhouse_clauses_honour_a_weekday_list():
    frag, params, n = dow_clause("date", _ctx("mon,wed"), 3)
    assert frag == "EXTRACT(ISODOW FROM date::date) IN (1, 3)"
    assert (params, n) == ([], 3)
    ch_frag, ch_params = dow_clause_ch(_ctx("mon,wed"))
    assert ch_frag == "toDayOfWeek(toDate(captured_at, 'Asia/Tokyo')) IN (1, 3)"
    assert ch_params == {}


def test_legacy_groups_keep_their_clauses():
    assert dow_clause("date", _ctx("weekday"), 1)[0] == "EXTRACT(ISODOW FROM date::date) BETWEEN 1 AND 5"
    assert dow_clause("date", _ctx("weekend"), 1)[0] == "EXTRACT(ISODOW FROM date::date) IN (6, 7)"
    assert dow_clause("date", _ctx("all"), 1)[0] == "TRUE"


def test_overview_python_matcher_honours_a_weekday_list():
    assert _dow_matches(date(2026, 9, 7), "mon,wed")  # a Monday
    assert not _dow_matches(date(2026, 9, 8), "mon,wed")  # a Tuesday


def test_clamp_range_ctx_canonicalises_dow():
    assert clamp_range_ctx(from_=None, to=None, dow="fri,mon").dow == "mon,fri"
