ALTER TABLE static_stops
    DROP COLUMN IF EXISTS platform_code,
    DROP COLUMN IF EXISTS stop_code;
