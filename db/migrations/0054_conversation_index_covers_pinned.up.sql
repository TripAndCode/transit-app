-- 0052 added (user_id, agency_id, updated_at DESC) for ask_conversations, but
-- list_conversations orders by `pinned DESC, updated_at DESC`. With `pinned`
-- absent the index can narrow the rows and still leaves the planner to sort
-- them, so the ordering half of that index bought nothing.
--
-- Replaced rather than edited in place: 0052 is already applied wherever this
-- has been deployed, and an applied migration is never re-run, so amending its
-- file would leave those databases on the old definition while fresh ones got
-- the new one.
DROP INDEX IF EXISTS idx_ask_conversations_user_agency_updated;

CREATE INDEX IF NOT EXISTS idx_ask_conversations_user_agency_pinned_updated
    ON ask_conversations (user_id, agency_id, pinned DESC, updated_at DESC);
