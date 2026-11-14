-- DESTRUCTIVE: drops rt_validation_daily, the ingested daily GTFS-RT validator
-- summaries, and feed_quality_monthly's validator columns. The summaries are
-- read again from R2 by the next ML store ingest, and the next analyze of each
-- agency rebuilds the columns, once the migration is applied again.
ALTER TABLE feed_quality_monthly DROP CONSTRAINT IF EXISTS feed_quality_monthly_validator_check;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_ingested_at;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_missing;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_rules;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_files_validated;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_failed_days;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS validator_ok_days;
DROP TABLE IF EXISTS rt_validation_daily;
