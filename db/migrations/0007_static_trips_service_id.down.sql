-- DESTRUCTIVE: drops static_trips.service_id.
ALTER TABLE static_trips
    DROP COLUMN IF EXISTS service_id;
