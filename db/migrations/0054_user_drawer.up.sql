-- Admin user drawer: hash-based API keys, revocation, and pre-approved invites.
--
-- NOTE: task A7 (#521, not yet merged) hashes sessions/api_keys with a
-- different shape (hashed session ids). This migration is additive-only so
-- it can be reconciled with A7 without conflicting column definitions: it
-- adds the columns this task needs to the api_keys table as it exists today
-- (raw ``key`` as the primary key, per db/migrations/0003_api_keys.up.sql),
-- rather than assuming A7's hashed-session-id schema.

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS id BIGSERIAL,
    ADD COLUMN IF NOT EXISTS key_hash TEXT,
    ADD COLUMN IF NOT EXISTS owner_user_id INT REFERENCES users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS label TEXT,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_id ON api_keys(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash) WHERE key_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_owner_user_id ON api_keys(owner_user_id) WHERE owner_user_id IS NOT NULL;

-- Admin-issued invites, pre-approving a role and LLM access for an email
-- that hasn't signed in yet. Honored by the OAuth callback on first login
-- for that email (see api/routers/auth.py's ``_upsert_user``).
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

-- Widen login_events.kind for sessions/api-keys/invites audit events.
ALTER TABLE login_events DROP CONSTRAINT IF EXISTS login_events_kind_check;
ALTER TABLE login_events ADD CONSTRAINT login_events_kind_check CHECK (
    kind IN (
        'login', 'logout', 'role_changed', 'suspended', 'unsuspended', 'deleted',
        'account_created', 'login_failed',
        'agency_created', 'agency_updated', 'agency_deleted', 'agency_restored',
        'llm_approved_changed',
        'session_revoked', 'api_key_issued', 'api_key_revoked',
        'invite_created', 'invite_consumed'
    )
);
