-- DESTRUCTIVE: drops planned_trips_daily.planned_stop_events, the saved stop
-- events of each day's plan, which no run can reconstruct once the timetable
-- that planned a day is replaced. Also drops feed_quality_monthly, which the
-- next analyze of each agency would rebuild after the migration is applied
-- again.
ALTER TABLE planned_trips_daily DROP COLUMN IF EXISTS planned_stop_events;
DROP TABLE IF EXISTS feed_quality_monthly;
