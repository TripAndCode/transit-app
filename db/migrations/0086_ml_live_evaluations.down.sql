-- DESTRUCTIVE: drops ml_live_evaluations; the next ingest (the cron sweep or the
-- operator ingest command) restores every week from R2's evaluations/ prefix,
-- which keeps each week's live.json.
DROP TABLE IF EXISTS ml_live_evaluations;
