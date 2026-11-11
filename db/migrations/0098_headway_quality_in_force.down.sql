-- DESTRUCTIVE: drops the scheduled headway sums kept per timetable in
-- route_headway_by_content. Those of timetables no longer loaded are lost for
-- good, and the loaded one's are not recorded again while its row at the
-- current agg_route_headway version stands. agg_route_headway_daily's
-- columns are derived, and analyze rebuilds them once the migration is
-- applied again.
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS scheduled_headway_sumsq_sec2;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS scheduled_headway_sum_sec;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS is_high_frequency;
ALTER TABLE agg_route_headway_daily DROP COLUMN IF EXISTS scheduled_headway_median_sec;
ALTER TABLE route_headway_by_content DROP COLUMN IF EXISTS scheduled_headway_sumsq_sec2;
ALTER TABLE route_headway_by_content DROP COLUMN IF EXISTS scheduled_headway_sum_sec;
