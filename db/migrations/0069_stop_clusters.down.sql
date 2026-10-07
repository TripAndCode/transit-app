-- DESTRUCTIVE: drops stop_clusters. Its rows are derived from static_stops
-- and come back on each agency's next load_static (or by re-applying this
-- migration), but a release at this version reads them for every heatmap.
DROP TABLE IF EXISTS stop_clusters;
