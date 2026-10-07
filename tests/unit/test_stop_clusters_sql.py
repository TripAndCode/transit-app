"""The cluster builder owns the DBSCAN; the heatmap only joins its result."""

import inspect

from api.routers import map as map_mod
from pipeline import stop_clusters


def test_builder_sql_partitions_by_name_key_with_the_tuned_eps():
    delete, insert = stop_clusters.stop_clusters_statements("%s")
    assert delete == "DELETE FROM stop_clusters WHERE agency_id = %s"
    assert "ST_ClusterDBSCAN(geom, eps := 0.005, minpoints := 1) OVER (PARTITION BY name_key)" in insert
    assert "CASE WHEN NULLIF(stop_name, '') IS NOT NULL THEN stop_name ELSE 'unnamed:' || stop_id END" in insert
    assert "WHERE agency_id = %s AND geom IS NOT NULL" in insert
    assert insert.count("%s") == 2  # agency_id projected and filtered


def test_builder_sql_takes_an_asyncpg_placeholder_too():
    _, insert = stop_clusters.stop_clusters_statements("$1")
    assert insert.count("$1") == 2 and "%s" not in insert


def test_heatmap_no_longer_clusters_per_request():
    source = inspect.getsource(map_mod.delay_heatmap)
    assert "ST_ClusterDBSCAN" not in source
    assert "FROM stop_clusters c" in source
    assert "JOIN static_stops s ON s.agency_id = c.agency_id AND s.stop_id = c.stop_id" in source
