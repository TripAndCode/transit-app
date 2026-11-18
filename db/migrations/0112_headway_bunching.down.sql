-- The bunching counts are derived. Applying the migration again re-creates the
-- columns empty while agg_table_state keeps the table's fingerprint, so refill
-- every date from the retention floor on with `make analyze-all FULL=1` (or a
-- registry version bump for the table).
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS bunching_judged;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS bunched_count;
