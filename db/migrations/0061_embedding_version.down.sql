-- DESTRUCTIVE: drops the embedding_version columns of ask_intent_cache and rag_chunks.
ALTER TABLE ask_intent_cache DROP COLUMN IF EXISTS embedding_version;
ALTER TABLE rag_chunks DROP COLUMN IF EXISTS embedding_version;
