-- DESTRUCTIVE: drops ask_intent_cache and the ask_query_log cache columns.
ALTER TABLE ask_query_log DROP COLUMN IF EXISTS cache_outcome;
ALTER TABLE ask_query_log DROP COLUMN IF EXISTS signature_hash;
DROP TABLE IF EXISTS ask_intent_cache;
