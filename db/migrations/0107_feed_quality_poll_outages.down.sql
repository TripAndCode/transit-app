-- DESTRUCTIVE: drops feed_quality_monthly's poll success and outage columns.
-- The next analyze of each agency rebuilds them after the migration is
-- applied again, except a month before its agency's retention floor.
ALTER TABLE feed_quality_monthly DROP CONSTRAINT IF EXISTS feed_quality_monthly_poll_outages_check;
ALTER TABLE feed_quality_monthly
    DROP COLUMN IF EXISTS peer_archive_rows,
    DROP COLUMN IF EXISTS outage_missing,
    DROP COLUMN IF EXISTS polls_missing,
    DROP COLUMN IF EXISTS collector_outage_longest_min,
    DROP COLUMN IF EXISTS collector_outage_min,
    DROP COLUMN IF EXISTS feed_outage_longest_min,
    DROP COLUMN IF EXISTS feed_outage_min,
    DROP COLUMN IF EXISTS poll_success_pct,
    DROP COLUMN IF EXISTS polls_expected,
    DROP COLUMN IF EXISTS polls_in_service,
    DROP COLUMN IF EXISTS poll_interval_sec,
    DROP COLUMN IF EXISTS in_service_min,
    DROP COLUMN IF EXISTS poll_days;
