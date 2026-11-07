-- Each date's rows by the ingest source they came from: the collector's archive
-- or the live path promotion copied into `updates`, told apart by
-- pipeline.clickhouse.LIVE_SOURCED. The two add up to total_rows.
--
-- NULL means the row was built before these columns existed. No backfill is
-- needed: agg_feed_health's logic version moved with them, so the next
-- analyze() of each agency rebuilds every date of it.
ALTER TABLE agg_feed_health
    ADD COLUMN IF NOT EXISTS archive_rows BIGINT,
    ADD COLUMN IF NOT EXISTS live_rows BIGINT;
