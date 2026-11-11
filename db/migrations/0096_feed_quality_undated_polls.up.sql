-- The feed-quality scorecard's message age counts each archived poll at the
-- instant its file name gives (pipeline.feed_quality). An archived poll whose
-- name gives none is left out of the ages, and this counts it beside them.
-- A month built before the column existed reads 0 until analyze rebuilds it,
-- which the registry's version for the table requires.
ALTER TABLE feed_quality_monthly ADD COLUMN IF NOT EXISTS message_age_undated_polls INT NOT NULL DEFAULT 0;
