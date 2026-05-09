-- Restore NOT NULL on updates. Any existing NULL rows must be fixed by hand
-- before rolling back; this is intentionally strict.
ALTER TABLE updates
    ALTER COLUMN service_type SET NOT NULL,
    ALTER COLUMN scheduled_time SET NOT NULL,
    ALTER COLUMN route_code SET NOT NULL;

ALTER TABLE agencies
    DROP COLUMN IF EXISTS static_strategy,
    DROP COLUMN IF EXISTS ingest_strategy;
