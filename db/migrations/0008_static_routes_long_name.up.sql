ALTER TABLE static_routes
    ADD COLUMN IF NOT EXISTS route_long_name TEXT;
