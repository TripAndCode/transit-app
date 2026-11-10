-- DESTRUCTIVE: drops planned_trips_daily.planned_trips_by_route, the saved
-- split of each day's plan by route, which no run can reconstruct once the
-- timetable that planned a day is replaced. agg_service_delivered_daily keeps
-- each day's count, its routes summed; the next analyze after the migration is
-- applied again rebuilds the route grain.
ALTER TABLE planned_trips_daily DROP COLUMN IF EXISTS planned_trips_by_route;

CREATE TEMP TABLE service_delivered_by_day ON COMMIT DROP AS
    SELECT agency_id, date, SUM(non_executed_trips)::int AS non_executed_trips
    FROM agg_service_delivered_daily
    GROUP BY agency_id, date;
DELETE FROM agg_service_delivered_daily;
ALTER TABLE agg_service_delivered_daily DROP CONSTRAINT agg_service_delivered_daily_pkey;
ALTER TABLE agg_service_delivered_daily DROP COLUMN route_code;
ALTER TABLE agg_service_delivered_daily ADD PRIMARY KEY (agency_id, date);
INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips)
    SELECT agency_id, date, non_executed_trips FROM service_delivered_by_day;
