-- Signed-out request counts by route: the cookie-free usage signal for
-- readers, who never sign in or hold an API key. One row per (Asia/Tokyo
-- day, route template, method, agency); api/activity.py adds to it in
-- batches alongside user_activity_daily. Holds no personal data -- no
-- address, hash, cookie or caller identity, only route templates and a
-- request/error count -- so it carries no retention prune. Day grain on
-- purpose: finer timestamps could be joined against ask_query_log or server
-- logs and narrow an otherwise anonymous count back down to one caller.
CREATE TABLE IF NOT EXISTS anonymous_activity_daily (
    day       DATE NOT NULL,
    route     TEXT NOT NULL,
    method    TEXT NOT NULL,
    agency_id INT,
    requests  INT  NOT NULL,
    errors    INT  NOT NULL
);

-- The upsert's arbiter. COALESCE because PG14 has no NULLS NOT DISTINCT and a
-- NULL agency_id would otherwise never conflict; agency ids start at 1.
CREATE UNIQUE INDEX IF NOT EXISTS anonymous_activity_daily_key
    ON anonymous_activity_daily (day, route, method, (COALESCE(agency_id, 0)));
