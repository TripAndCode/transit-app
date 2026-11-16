-- The feed-quality scorecard's poll success and outage minutes
-- (pipeline.poll_outages): over the days counted (poll_days), the in-service
-- time, the poll interval, the polls recorded and expected and their share,
-- and the outage minutes inside service, the feed's apart from the shared
-- collector's, each with its longest. polls_missing says why the poll figures
-- are empty, and outage_missing why the outage minutes are, which is the same
-- reason or no_peer. peer_rows is the other listed agencies' rows around
-- the window the split was read against, so a month is rebuilt when it moves.
--
-- A month built before the columns existed holds none of them, never a 0
-- that would read as measured, until analyze rebuilds it, which the
-- registry's version for the table requires.
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS poll_days INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS in_service_min NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS poll_interval_sec NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS polls_in_service INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS polls_expected INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS poll_success_pct NUMERIC(4, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS feed_outage_min NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS feed_outage_longest_min NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS collector_outage_min NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS collector_outage_longest_min NUMERIC(8, 1);
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS polls_missing TEXT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS outage_missing TEXT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS peer_rows BIGINT;
ALTER TABLE feed_quality_monthly ADD CONSTRAINT feed_quality_monthly_poll_outages_check
    CHECK (
        (poll_days IS NULL AND in_service_min IS NULL AND poll_interval_sec IS NULL AND polls_in_service IS NULL
         AND polls_expected IS NULL AND poll_success_pct IS NULL AND feed_outage_min IS NULL
         AND feed_outage_longest_min IS NULL AND collector_outage_min IS NULL
         AND collector_outage_longest_min IS NULL AND polls_missing IS NULL AND outage_missing IS NULL
         AND peer_rows IS NULL)
        OR (poll_days IS NOT NULL AND peer_rows IS NOT NULL
            AND (poll_success_pct IS NULL) = (polls_missing IS NOT NULL)
            AND (in_service_min IS NULL) = (poll_success_pct IS NULL)
            AND (poll_interval_sec IS NULL) = (poll_success_pct IS NULL)
            AND (polls_in_service IS NULL) = (poll_success_pct IS NULL)
            AND (polls_expected IS NULL) = (poll_success_pct IS NULL)
            AND poll_success_pct BETWEEN 0 AND 100
            AND (feed_outage_min IS NULL) = (outage_missing IS NOT NULL)
            AND (feed_outage_longest_min IS NULL) = (feed_outage_min IS NULL)
            AND (collector_outage_min IS NULL) = (feed_outage_min IS NULL)
            AND (collector_outage_longest_min IS NULL) = (feed_outage_min IS NULL)
            AND (polls_missing IS NULL OR outage_missing = polls_missing))
    );
