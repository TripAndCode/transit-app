-- The feed-quality scorecard counts each month's polls whose message was more
-- than 65 s old when captured, and their share of the polls measured
-- (pipeline.feed_quality). The share is stated exactly when message age is.
-- A month built before the columns existed holds neither, never a 0 that
-- would read as measured, until analyze rebuilds it, which the registry's
-- version for the table requires. The check binds the rows written from now
-- on, so it comes NOT VALID.
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS message_age_stale_polls INT;
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS message_age_stale_pct NUMERIC(5, 2);
ALTER TABLE feed_quality_monthly ADD CONSTRAINT feed_quality_monthly_stale_share_check
    CHECK (
        (message_age_stale_pct IS NULL) = (message_age_missing IS NOT NULL)
        AND (message_age_stale_polls IS NULL) = (message_age_stale_pct IS NULL)
    ) NOT VALID;
