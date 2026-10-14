-- DESTRUCTIVE: drops static_routes' origin_name, via_name, destination_name,
-- is_loop and bidirectional. The next load_static of each agency recomputes
-- them once 0074 is reapplied.
ALTER TABLE static_routes
    DROP COLUMN IF EXISTS origin_name,
    DROP COLUMN IF EXISTS via_name,
    DROP COLUMN IF EXISTS destination_name,
    DROP COLUMN IF EXISTS is_loop,
    DROP COLUMN IF EXISTS bidirectional;
