"""Rendered-SQL and pure-projection guards for the aggregate read paths.

These assert the SQL text the API builds (and the Python projection applied to
its rows), not query results — they need no database.
"""

import inspect

import api.routers.reports as reports_mod
from api.routers.map import _HEATMAP_CLUSTER_PROJECTION_SQL, _heatmap_features
from api.routers.overview import _peak_hour_breakdown_sql
from api.routers.reports import _POOLED_DELAY_PROJECTION_SQL


def _heatmap_row(**over):
    row = {
        "lon": 132.45,
        "lat": 34.39,
        "stop_name": "本通",
        "stop_ids": "S1",
        "platform_codes": "",
        "stop_codes": "",
        "route_codes": "1",
        "avg_delay_min": 2.5,
        "p90_delay_min": 6.0,
        "samples": 120,
    }
    row.update(over)
    return row


def test_heatmap_cluster_projection_guards_zero_samples():
    assert "NULLIF(SUM(samples), 0)" in _HEATMAP_CLUSTER_PROJECTION_SQL
    assert "/ SUM(samples) /" not in _HEATMAP_CLUSTER_PROJECTION_SQL


def test_heatmap_feature_tolerates_null_avg_delay():
    fc = _heatmap_features([_heatmap_row(avg_delay_min=None)])
    assert fc["features"][0]["properties"]["avg_delay_min"] is None


def test_heatmap_feature_keeps_numeric_avg_delay():
    fc = _heatmap_features([_heatmap_row()])
    assert fc["features"][0]["properties"]["avg_delay_min"] == 2.5


def test_pooled_delay_projection_counts_only_the_averaged_population():
    assert "SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples" in _POOLED_DELAY_PROJECTION_SQL


def test_reports_never_reports_an_unfiltered_sample_count():
    assert "SUM(samples)::int AS samples" not in inspect.getsource(reports_mod)


def test_peak_hour_sql_derives_avg_from_sums_in_both_branches():
    for by_dow in (True, False):
        sql = _peak_hour_breakdown_sql(by_dow=by_dow)
        assert "SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)" in sql
        assert "NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0)" in sql
        assert "GROUP BY route_code, service_type" in sql
        assert "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) >= 3" in sql


def test_peak_hour_sql_applies_the_sample_floor_only_to_the_group():
    for by_dow in (True, False):
        assert "samples >= 3" not in _peak_hour_breakdown_sql(by_dow=by_dow).split("HAVING")[0]


def test_peak_hour_sql_scopes_the_dow_only_when_asked():
    assert "dow = $3" in _peak_hour_breakdown_sql(by_dow=True)
    assert "dow = $" not in _peak_hour_breakdown_sql(by_dow=False)


def test_peak_hour_sql_never_selects_the_stored_avg_min():
    """The only avg_min in either select list is the one derived from the sums."""
    for by_dow in (True, False):
        select_list = _peak_hour_breakdown_sql(by_dow=by_dow).split("FROM agg_route_hour_dow")[0]
        assert select_list.count("avg_min") == 1
        assert "/ 60.0) AS avg_min" in select_list


def test_peak_hour_sql_counts_only_the_averaged_population():
    """`samples` is the evidence behind `avg_min`, so it has to exclude rows
    the average itself excluded. A row can carry a sample count with no delay
    sum behind it, and counting those would overstate how much observation the
    displayed figure rests on -- and let a route clear the floor on rows that
    contributed nothing to its average."""
    for by_dow in (True, False):
        sql = _peak_hour_breakdown_sql(by_dow=by_dow)
        assert "SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) AS samples" in sql
        assert "SUM(samples) AS samples" not in sql


def test_movers_counts_only_the_averaged_population():
    """The movers card shows `samples` as the evidence behind its current
    average, so it must exclude rows that average excluded."""
    import pipeline.dashboard_queries as dq

    src = inspect.getsource(dq)
    assert "COALESCE(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) AS n" in src
    assert "SUM(samples) AS n" not in src


def test_route_hour_dow_pattern_counts_only_the_averaged_population():
    """Same rule for the Ask tool's time-pattern query, including its floor:
    a group must not clear the threshold on rows that contributed nothing to
    the average it is shown beside."""
    import pipeline.query.tool_queries as tq

    src = inspect.getsource(tq)
    assert "SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples" in src
    assert "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 5" in src
    assert "SUM(samples)::int AS samples" not in src
