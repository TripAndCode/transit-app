-- Back to 0052's definition.
DROP INDEX IF EXISTS idx_ask_conversations_user_agency_pinned_updated;

CREATE INDEX IF NOT EXISTS idx_ask_conversations_user_agency_updated
    ON ask_conversations (user_id, agency_id, updated_at DESC);
