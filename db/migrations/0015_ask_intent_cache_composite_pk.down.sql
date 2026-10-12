-- DESTRUCTIVE: deletes the ask_intent_cache rows that share a signature_hash across agencies, keeping the most-used one.
-- The single-column key cannot hold the same signature twice; the cache is rebuilt on demand.
DROP INDEX IF EXISTS ix_ask_intent_cache_last_question;
DELETE FROM ask_intent_cache c
USING (
  SELECT DISTINCT ON (signature_hash) signature_hash, agency_id
  FROM ask_intent_cache
  ORDER BY signature_hash, hit_count DESC, last_used_at DESC, agency_id
) keep
WHERE c.signature_hash = keep.signature_hash AND c.agency_id <> keep.agency_id;
ALTER TABLE ask_intent_cache DROP CONSTRAINT IF EXISTS ask_intent_cache_pkey;
ALTER TABLE ask_intent_cache ADD PRIMARY KEY (signature_hash);
