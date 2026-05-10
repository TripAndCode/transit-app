ALTER TABLE static_trips
    ADD COLUMN IF NOT EXISTS service_id TEXT;
