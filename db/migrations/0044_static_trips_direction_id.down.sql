-- DESTRUCTIVE: drops static_trips.direction_id.
ALTER TABLE static_trips
DROP COLUMN IF EXISTS direction_id;
