-- A TIME cannot hold a time past 24:00, and casting one wraps it onto a row of
-- the same route and service that may already exist, so those rows go first.
-- An analyze run under the TIME column never builds them.
DELETE FROM agg_route_hour WHERE scheduled_time >= INTERVAL '24 hours';
ALTER TABLE agg_route_hour
  ALTER COLUMN scheduled_time TYPE TIME USING scheduled_time::time;
