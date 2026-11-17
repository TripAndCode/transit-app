-- Applying the migration again re-creates the table empty; agg_table_state still
-- holds its row, so refill every date with `make analyze-all FULL=1`.
DROP TABLE IF EXISTS agg_route_segment_runtime;
