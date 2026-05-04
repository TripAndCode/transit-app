-- Surface platform_code (停留所のポール番号) and stop_code (停留所コード)
-- from raw GTFS so the map tooltip can show "②のりば" / pole 2 etc.
-- Both are GTFS-spec optional fields, populated by static_loader.

ALTER TABLE static_stops
    ADD COLUMN IF NOT EXISTS stop_code     TEXT,
    ADD COLUMN IF NOT EXISTS platform_code TEXT;
