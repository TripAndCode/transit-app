DROP INDEX IF EXISTS idx_users_name_trgm;
DROP INDEX IF EXISTS idx_users_email_trgm;
-- 0054 drops and replaces this same index; roll that back first if it is applied.
DROP INDEX IF EXISTS idx_ask_conversations_user_agency_updated;
DROP INDEX IF EXISTS idx_login_events_user_created;
