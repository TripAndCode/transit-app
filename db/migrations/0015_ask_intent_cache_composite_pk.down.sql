DROP INDEX IF EXISTS ix_ask_intent_cache_last_question;
ALTER TABLE ask_intent_cache DROP CONSTRAINT IF EXISTS ask_intent_cache_pkey;
ALTER TABLE ask_intent_cache ADD PRIMARY KEY (signature_hash);
