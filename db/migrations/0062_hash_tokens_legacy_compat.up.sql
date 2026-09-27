-- Keeps a release that predates 0053_hash_tokens working against its schema.
-- `migrate up` runs while the previous release is still serving, and rolling
-- the app back does not roll the schema back, so that release's SQL meets the
-- hashed tables. It writes a session with only the raw `sid` (an operator
-- provisions an API key the same way, with only the raw `key`), which the NOT
-- NULL `sid_hash` / `key_hash` primary keys reject, and it looks both up by
-- the raw value, whose index went with the old primary keys.
--
--   - A BEFORE INSERT trigger fills in the digest when the writer supplied the
--     raw value and no digest, using the expression 0053 backfilled with, which
--     is what `api.security.token_hash` computes -- so the row is also found by
--     a release that looks it up by digest. A supplied digest is never
--     replaced. INSERT only: no release updates `sid` or `key` in place.
--   - idx_sessions_sid / idx_api_keys_key restore an index on each raw column
--     for `WHERE sid = $1` / `WHERE key = $1`. Partial, because rows written
--     by a digest-only writer have no raw value; unique, as the old primary
--     keys were. A partial index cannot be an ON CONFLICT arbiter, and no
--     release that predates 0053 uses ON CONFLICT on either table.
--
-- The trigger functions read NEW.sid / NEW.key: dropping a raw column while
-- they exist makes every INSERT into its table fail. See db/migrations/README.md.

CREATE OR REPLACE FUNCTION sessions_fill_sid_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sid_hash IS NULL AND NEW.sid IS NOT NULL THEN
        NEW.sid_hash := encode(sha256(convert_to(NEW.sid, 'UTF8')), 'hex');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sessions_fill_sid_hash ON sessions;
CREATE TRIGGER sessions_fill_sid_hash
    BEFORE INSERT ON sessions
    FOR EACH ROW EXECUTE FUNCTION sessions_fill_sid_hash();

CREATE OR REPLACE FUNCTION api_keys_fill_key_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.key_hash IS NULL AND NEW.key IS NOT NULL THEN
        NEW.key_hash := encode(sha256(convert_to(NEW.key, 'UTF8')), 'hex');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS api_keys_fill_key_hash ON api_keys;
CREATE TRIGGER api_keys_fill_key_hash
    BEFORE INSERT ON api_keys
    FOR EACH ROW EXECUTE FUNCTION api_keys_fill_key_hash();

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_sid
    ON sessions (sid) WHERE sid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key
    ON api_keys (key) WHERE key IS NOT NULL;
