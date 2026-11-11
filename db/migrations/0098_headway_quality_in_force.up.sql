-- Each day's headway quality is judged by the timetable in force that day
-- (pipeline.static_headways, pipeline.reports.headway_quality).
--
-- route_headway_by_content keeps, beside each timetable's median, the sum and
-- sum of squares of its scheduled gaps per route: the scheduled mean wait of
-- days pooled under several timetables is the sum of their sums of squares
-- over twice the sum of their sums, which neither a median nor a mean wait
-- can give. A timetable kept before this has its median only: once replaced,
-- Postgres can no longer give its sums.
--
-- agg_route_headway_daily carries each (route, date)'s own timetable's
-- median, high-frequency test and sums. is_high_frequency is NULL when that
-- timetable keeps no scheduled headways, and the date is then not judged. A
-- row built before this reads NULL until analyze rebuilds it, which the
-- registry's version for the table requires.
ALTER TABLE route_headway_by_content ADD COLUMN IF NOT EXISTS scheduled_headway_sum_sec DOUBLE PRECISION;
ALTER TABLE route_headway_by_content ADD COLUMN IF NOT EXISTS scheduled_headway_sumsq_sec2 DOUBLE PRECISION;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS scheduled_headway_median_sec REAL;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS is_high_frequency BOOLEAN;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS scheduled_headway_sum_sec DOUBLE PRECISION;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS scheduled_headway_sumsq_sec2 DOUBLE PRECISION;
