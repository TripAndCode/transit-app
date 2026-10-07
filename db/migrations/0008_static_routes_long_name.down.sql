-- DESTRUCTIVE: drops static_routes.route_long_name.
ALTER TABLE static_routes
    DROP COLUMN IF EXISTS route_long_name;
