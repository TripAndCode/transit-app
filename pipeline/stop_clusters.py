"""Stop clusters for the heatmap: which platforms of one named stop draw as one dot.

Computed from ``static_stops`` alone, so it is rebuilt where that table is
rewritten (``static_loader.load_static``) and only read by the API. Two
platforms merge when they share a ``name_key`` and lie within
``STOP_CLUSTER_EPS_DEG`` of each other (DBSCAN with every point a core point,
so clusters chain transitively within the name partition). An unnamed stop
gets a key unique to itself and therefore never merges; a stop without
geometry has no row.

Rows are only rebuilt when an agency's ``stops.txt`` loads, so changing
``STOP_CLUSTER_EPS_DEG`` or the ``name_key`` rule needs a migration that
rebuilds every agency's rows; otherwise the heatmap mixes old and new rules.
"""

#: ~550 m at Japan's latitudes. Sized as a merge radius for platforms of one
#: stop, not as a grid cell: with every point a core point, an eps the size of
#: a kilometre-scale cell chains unrelated same-named stops across a city.
STOP_CLUSTER_EPS_DEG = 0.005

_NAME_KEY_SQL = "CASE WHEN NULLIF(stop_name, '') IS NOT NULL THEN stop_name ELSE 'unnamed:' || stop_id END"


def stop_clusters_statements(param: str) -> tuple[str, str]:
    """The (DELETE, INSERT ... SELECT) pair that rebuilds one agency's rows.

    *param* is the driver's placeholder for the agency id (``%s`` for
    psycopg2, ``$1`` for asyncpg); both statements bind it, the INSERT twice.
    """
    delete = f"DELETE FROM stop_clusters WHERE agency_id = {param}"
    insert = (
        "INSERT INTO stop_clusters (agency_id, stop_id, name_key, cluster_id)\n"
        f"SELECT {param}, stop_id, name_key,\n"
        f"       ST_ClusterDBSCAN(geom, eps := {STOP_CLUSTER_EPS_DEG}, minpoints := 1) OVER (PARTITION BY name_key)\n"
        "FROM (\n"
        f"    SELECT stop_id, geom, {_NAME_KEY_SQL} AS name_key\n"
        "    FROM static_stops\n"
        f"    WHERE agency_id = {param} AND geom IS NOT NULL\n"
        ") named"
    )
    return delete, insert


def rebuild_stop_clusters(cur, agency_id: int) -> int:
    """Rebuild *agency_id*'s rows on a psycopg2 cursor; the caller commits."""
    delete, insert = stop_clusters_statements("%s")
    cur.execute(delete, (agency_id,))
    cur.execute(insert, (agency_id, agency_id))
    return cur.rowcount
