-- DESTRUCTIVE: drops feed_quality_monthly.message_age_stale_polls and
-- message_age_stale_pct, each month's count and share of polls whose message
-- was more than 65 s old when captured. The next analyze of each agency
-- rebuilds them after the migration is applied again.
ALTER TABLE feed_quality_monthly DROP CONSTRAINT IF EXISTS feed_quality_monthly_stale_share_check;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS message_age_stale_pct;
ALTER TABLE feed_quality_monthly DROP COLUMN IF EXISTS message_age_stale_polls;
