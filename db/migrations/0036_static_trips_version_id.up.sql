ALTER TABLE static_trips
    ADD COLUMN IF NOT EXISTS static_version_id TEXT;
