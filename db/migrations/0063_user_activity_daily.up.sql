-- Per-user, per-day request counts by route: the usage record the admin
-- user page reads and later plan limits can meter. One row per (user,
-- Asia/Tokyo day, route template, method, agency, API key or session);
-- api/activity.py adds to it in batches. Route templates, never raw paths,
-- so no question text, route code or search term is stored. Day grain on
-- purpose: finer timestamps could be joined against ask_query_log's and undo
-- that table's anonymity.
CREATE TABLE IF NOT EXISTS user_activity_daily (
    user_id     INT     NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    day         DATE    NOT NULL,
    route       TEXT    NOT NULL,
    method      TEXT    NOT NULL,
    agency_id   INT,
    via_api_key BOOLEAN NOT NULL,
    requests    INT     NOT NULL,
    errors      INT     NOT NULL
);

-- The upsert's arbiter, and the index the admin page's per-user range read
-- uses. COALESCE because PG14 has no NULLS NOT DISTINCT and a NULL agency_id
-- would otherwise never conflict; agency ids start at 1.
CREATE UNIQUE INDEX IF NOT EXISTS user_activity_daily_key
    ON user_activity_daily (user_id, day, route, method, (COALESCE(agency_id, 0)), via_api_key);
