-- Composite/trigram indexes for query shapes that previously fell back to a
-- sequential scan or a single-column index that couldn't answer the full
-- filter:
--   - login_events is queried per-user ordered by recency (audit trail /
--     admin user-detail view); the existing single-column indexes on
--     user_id and created_at separately can't serve that as an index-only
--     ordered scan the way (user_id, created_at DESC) can.
--   - ask_conversations is listed per (user_id, agency_id) ordered by
--     updated_at (see pipeline/query/conversations.list_conversations);
--     the existing (user_id, updated_at DESC) index still has to filter
--     agency_id out of the result instead of narrowing to it in the index.
--   - users.email / users.name are searched with ILIKE '%...%' in the admin
--     user list, which pg_trgm's GIN opclass can serve; pg_trgm itself is
--     already enabled by 0012_pgtrgm_route_names.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_login_events_user_created
    ON login_events (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ask_conversations_user_agency_updated
    ON ask_conversations (user_id, agency_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_users_email_trgm
    ON users USING gin (email gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_users_name_trgm
    ON users USING gin (name gin_trgm_ops);
