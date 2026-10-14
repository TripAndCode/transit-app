-- Where each route runs, derived by load_static (pipeline/route_terminals.py)
-- so a display needs no per-request work: its terminal and via stop names
-- (NULL when the feed gives no way to name them), whether it loops back to
-- its origin, and whether its trips also run the way back.
ALTER TABLE static_routes
    ADD COLUMN IF NOT EXISTS origin_name TEXT,
    ADD COLUMN IF NOT EXISTS via_name TEXT,
    ADD COLUMN IF NOT EXISTS destination_name TEXT,
    ADD COLUMN IF NOT EXISTS is_loop BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS bidirectional BOOLEAN NOT NULL DEFAULT false;
