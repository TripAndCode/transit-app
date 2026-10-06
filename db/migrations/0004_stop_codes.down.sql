-- DESTRUCTIVE: drops static_stops.platform_code and stop_code.
ALTER TABLE static_stops
    DROP COLUMN IF EXISTS platform_code,
    DROP COLUMN IF EXISTS stop_code;
