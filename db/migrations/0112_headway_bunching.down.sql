-- The bunching counts are derived; analyze rebuilds them once the migration
-- is applied again, for every date from the retention floor on.
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS bunching_judged;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS bunched_count;
