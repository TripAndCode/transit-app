-- Restores the schema 0108 dropped, empty: the reader accounts, their sign-in
-- history, presets, LLM keys, invites, API keys and flag overrides it deleted
-- are not recovered. Every usage row comes back as through a session.
-- via_api_key returns as the table's last column rather than in its old
-- position; nothing reads the table by position.

DROP INDEX IF EXISTS user_activity_daily_key;
ALTER TABLE user_activity_daily ADD COLUMN IF NOT EXISTS via_api_key BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE user_activity_daily ALTER COLUMN via_api_key DROP DEFAULT;
CREATE UNIQUE INDEX IF NOT EXISTS user_activity_daily_key
    ON user_activity_daily (user_id, day, route, method, (COALESCE(agency_id, 0)), via_api_key);

CREATE TABLE IF NOT EXISTS api_keys (
    key           TEXT,
    owner_email   TEXT NOT NULL,
    tier          TEXT NOT NULL DEFAULT 'pro',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    key_hash      TEXT NOT NULL PRIMARY KEY,
    revoked_at    TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    id            BIGSERIAL,
    owner_user_id INT REFERENCES users(user_id) ON DELETE SET NULL,
    label         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_id ON api_keys(id);
CREATE INDEX IF NOT EXISTS idx_api_keys_owner_user_id ON api_keys(owner_user_id) WHERE owner_user_id IS NOT NULL;

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key
    ON api_keys (key) WHERE key IS NOT NULL;

CREATE TABLE IF NOT EXISTS user_invites (
    invite_id        SERIAL PRIMARY KEY,
    email            TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    llm_approved     BOOLEAN NOT NULL DEFAULT false,
    invited_by       INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '14 days'),
    consumed_at      TIMESTAMPTZ,
    consumed_user_id INT REFERENCES users(user_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_user_invites_email_pending
    ON user_invites (lower(email))
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS user_llm_keys (
    user_id       INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    provider      TEXT NOT NULL CONSTRAINT user_llm_keys_provider_check CHECK (provider IN ('openai', 'gemini')),
    encrypted_key BYTEA NOT NULL,
    key_suffix    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS filter_presets (
    preset_id    SERIAL PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    agency_id    INT NOT NULL REFERENCES agencies(agency_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    range_ctx    JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, agency_id, name)
);
CREATE INDEX IF NOT EXISTS idx_filter_presets_user_agency ON filter_presets(user_id, agency_id);
