-- DESTRUCTIVE: drops static_trips.static_version_id.
ALTER TABLE static_trips
    DROP COLUMN IF EXISTS static_version_id;
