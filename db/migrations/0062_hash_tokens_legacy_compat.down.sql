DROP INDEX IF EXISTS idx_api_keys_key;
DROP INDEX IF EXISTS idx_sessions_sid;

DROP TRIGGER IF EXISTS api_keys_fill_key_hash ON api_keys;
DROP FUNCTION IF EXISTS api_keys_fill_key_hash();

DROP TRIGGER IF EXISTS sessions_fill_sid_hash ON sessions;
DROP FUNCTION IF EXISTS sessions_fill_sid_hash();
