-- DESTRUCTIVE: drops feed_quality_monthly.message_age_undated_polls, each
-- month's count of archived polls whose file name gives no capture instant.
-- The next analyze of each agency rebuilds it after the migration is applied
-- again.
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS message_age_undated_polls;
