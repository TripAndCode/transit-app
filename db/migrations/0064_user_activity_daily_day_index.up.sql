-- The daily retention prune deletes by `day` alone; the table's only other
-- index (the upsert arbiter) leads with user_id and cannot serve that scan.
CREATE INDEX IF NOT EXISTS idx_user_activity_daily_day ON user_activity_daily (day);
