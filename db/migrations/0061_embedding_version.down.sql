ALTER TABLE ask_intent_cache DROP COLUMN IF EXISTS embedding_version;
ALTER TABLE rag_chunks DROP COLUMN IF EXISTS embedding_version;
