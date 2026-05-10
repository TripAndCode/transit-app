ALTER TABLE agencies
    ADD COLUMN IF NOT EXISTS ingest_strategy TEXT,
    ADD COLUMN IF NOT EXISTS static_strategy TEXT;

ALTER TABLE updates
    ALTER COLUMN service_type DROP NOT NULL,
    ALTER COLUMN scheduled_time DROP NOT NULL,
    ALTER COLUMN route_code DROP NOT NULL;
