"""Rendered-SQL and pure-projection guards for the aggregate read paths.

These assert the SQL text the API builds (and the Python projection applied to
its rows), not query results — they need no database.
"""

import inspect

import api.routers.reports as reports_mod
from api.routers.map import _HEATMAP_CLUSTER_PROJECTION_SQL, _heatmap_features
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


def test_route_hour_dow_pattern_counts_only_the_averaged_population():
    """Same rule for the Ask tool's time-pattern query, including its floor:
    a group must not clear the threshold on rows that contributed nothing to
    the average it is shown beside."""
    import pipeline.query.tool_queries as tq

    src = inspect.getsource(tq)
    assert "SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples" in src
    assert "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 5" in src
    assert "SUM(samples)::int AS samples" not in src


def test_heatmap_cluster_projection_still_groups_by_name_key_and_cluster_id():
    assert "GROUP BY name_key, cluster_id" in _HEATMAP_CLUSTER_PROJECTION_SQL
