-- The heatmap merges same-named platforms within ~550 m into one dot. That
-- grouping is a function of the static schedule alone, so it is computed once
-- per load (pipeline.stop_clusters, called by static_loader.load_static) and
-- joined at read time, instead of running ST_ClusterDBSCAN over every stop on
-- every request.
--
-- name_key is the partition a stop may merge within: its stop_name, or a
-- per-stop key for an unnamed stop so such stops never merge. cluster_id is
-- DBSCAN's label within that partition. A stop without geometry has no row.
CREATE TABLE IF NOT EXISTS stop_clusters (
    agency_id  INTEGER NOT NULL REFERENCES agencies(agency_id),
    stop_id    TEXT    NOT NULL,
    name_key   TEXT    NOT NULL,
    cluster_id INTEGER NOT NULL,
    PRIMARY KEY (agency_id, stop_id)
);

-- Backfill from every schedule already loaded, so the heatmap keeps its dots
-- through the deploy rather than waiting for each agency's next static load.
INSERT INTO stop_clusters (agency_id, stop_id, name_key, cluster_id)
SELECT agency_id, stop_id, name_key,
       ST_ClusterDBSCAN(geom, eps := 0.005, minpoints := 1) OVER (PARTITION BY agency_id, name_key)
FROM (
    SELECT agency_id, stop_id, geom,
           CASE WHEN NULLIF(stop_name, '') IS NOT NULL THEN stop_name ELSE 'unnamed:' || stop_id END AS name_key
    FROM static_stops
    WHERE geom IS NOT NULL
) named
ON CONFLICT (agency_id, stop_id) DO NOTHING;
