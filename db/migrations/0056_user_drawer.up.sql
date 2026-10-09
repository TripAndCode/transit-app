-- Admin user drawer: owned/labeled API keys and pre-approved invites.
--
-- 0053_hash_tokens already gave ``api_keys`` a ``key_hash`` primary key plus
-- ``revoked_at``/``expires_at``. This migration only adds what that one
-- didn't: a stable numeric ``id`` for admin UI references (the primary key
-- is a credential hash, not something to put in a URL), an owning user, and
-- a display label.

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS id BIGSERIAL,
    ADD COLUMN IF NOT EXISTS owner_user_id INT REFERENCES users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS label TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_id ON api_keys(id);
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
