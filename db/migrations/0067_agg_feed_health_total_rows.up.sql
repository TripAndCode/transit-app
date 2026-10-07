-- The per-date ledger analyze() compares against ClickHouse to decide which
-- service dates to rebuild. raw_samples counts only rows carrying a dep_delay;
-- total_rows counts every row of the date, so aggregates drawn from delay-less
-- rows (cancellations, skipped stops, static_version_id, a stop's route
-- coverage) can trust the ledger too.
--
-- NULL means the row was written before this column existed, which resolves
-- to a full rebuild on the next run -- so no backfill is needed.
ALTER TABLE agg_feed_health ADD COLUMN IF NOT EXISTS total_rows BIGINT;
