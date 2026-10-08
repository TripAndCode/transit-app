"""The agg date predicates must keep the column bare so the (agency_id, date)
index prefix serves the range scan. Asserted on rendered SQL; no database."""

import inspect
from datetime import date

from api.range import RangeCtx, date_range_clause
from pipeline import analyze as analyze_mod
from pipeline import dashboard_queries
from pipeline.reports import filters, overview, rankings


def _ctx(**over):
    base = dict(
        from_date=date(2026, 5, 13), to_date=date(2026, 6, 11), dow="all", time_band="all", service="all", routes=()
    )
    base.update(over)
    return RangeCtx(**base)


def test_date_range_clause_keeps_the_column_uncast():
    frag, params, n = date_range_clause("date", _ctx(), next_param=3)
    assert frag == "date >= ($3::text)::date AND date <= ($4::text)::date"
    assert params == ["2026-05-13", "2026-06-11"]
    assert n == 5


def test_agg_filter_and_dist_filter_render_the_same_predicate_shape():
    ctx = _ctx(dow="weekend", service="平日", routes=("R1",))
    assert filters._agg_filter(ctx, 2) == filters._dist_filter(ctx, 2)
    frag, _, _ = filters._agg_filter(ctx, 2)
    assert "date::date" not in frag
    assert frag.startswith("date >= ($2::text)::date AND date <= ($3::text)::date")


def test_no_reader_casts_the_trend_date_column():
    for mod in (overview, rankings, filters, dashboard_queries):
        source = inspect.getsource(mod)
        assert "date::date" not in source, mod.__name__


def test_analyze_projects_the_date_directly_and_has_no_text_dated_tables():
    source = inspect.getsource(analyze_mod)
    assert "to_char(date, 'YYYY-MM-DD')" not in source
    assert not hasattr(analyze_mod, "_text_dated_tables")
