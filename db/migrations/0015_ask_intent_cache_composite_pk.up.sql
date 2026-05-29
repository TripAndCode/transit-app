-- Make ask_intent_cache scopable per agency so two tenants asking the same
-- canonical question don't collide on signature_hash (which is intentionally
-- agency-agnostic in pipeline/query/intent.py). Also adds the missing index on
-- last_question for the pre-LLM exact-text lookup that fires on every cached
-- request.
ALTER TABLE ask_intent_cache DROP CONSTRAINT IF EXISTS ask_intent_cache_pkey;
ALTER TABLE ask_intent_cache ADD PRIMARY KEY (signature_hash, agency_id);
CREATE INDEX IF NOT EXISTS ix_ask_intent_cache_last_question
  ON ask_intent_cache (agency_id, last_question);
