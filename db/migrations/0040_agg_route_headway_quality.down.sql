ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS long_gap_count;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS actual_headway_sumsq_sec2;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS actual_headway_sum_sec;
ALTER TABLE agg_route_headway DROP COLUMN IF EXISTS scheduled_wait_mean_sec;
