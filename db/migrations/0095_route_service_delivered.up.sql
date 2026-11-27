-- The service-delivered figures gain a route grain, so a route's rate is read
-- on its own trips (pipeline.reports.service_delivered).
--
-- agg_service_delivered_daily counts each trip the feed reported as not run
-- under the one route its latest report naming a route names, '' where none
-- does, so an agency's day is the sum of its routes. The rows already here
-- keep their day's count under '' until analyze rebuilds the table, which the
-- registry's version for it requires.
ALTER TABLE agg_service_delivered_daily ADD COLUMN IF NOT EXISTS route_code TEXT NOT NULL DEFAULT '';
ALTER TABLE agg_service_delivered_daily DROP CONSTRAINT agg_service_delivered_daily_pkey;
ALTER TABLE agg_service_delivered_daily ADD PRIMARY KEY (agency_id, date, route_code);

-- Each saved day's planned trips by route, saved beside planned_trips by the
-- same capture (pipeline.planned_trips) from the same timetable, so a day's
-- routes sum to its planned_trips: route_code (the static route_id as
-- pipeline.route_catalog maps it, '' where a trip names none) to trips. NULL
-- on a day saved before the column existed: its timetable may be gone, so its
-- split is unknown and a route's rate does not guess it.
ALTER TABLE planned_trips_daily ADD COLUMN IF NOT EXISTS planned_trips_by_route JSONB;
