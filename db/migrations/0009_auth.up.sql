CREATE TABLE IF NOT EXISTS users (
    user_id      SERIAL PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    name         TEXT,
    avatar_url   TEXT,
    role         TEXT NOT NULL DEFAULT 'user'
                   CHECK (role IN ('user', 'admin')),
    suspended_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_identities (
    provider      TEXT NOT NULL,
    provider_sub  TEXT NOT NULL,
    user_id       INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    email_at_link TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_sub)
);
CREATE INDEX IF NOT EXISTS idx_oauth_identities_user_id ON oauth_identities(user_id);

CREATE TABLE IF NOT EXISTS sessions (
    sid          TEXT PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    user_agent   TEXT,
    ip           INET
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_events (
    event_id     BIGSERIAL PRIMARY KEY,
    user_id      INT REFERENCES users(user_id) ON DELETE SET NULL,
    actor_id     INT REFERENCES users(user_id) ON DELETE SET NULL,
    kind         TEXT NOT NULL CHECK (kind IN (
        'login', 'logout', 'role_changed', 'suspended', 'unsuspended', 'deleted'
    )),
    provider     TEXT,
    meta         JSONB,
    ip           INET,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_login_events_user_id ON login_events(user_id);
CREATE INDEX IF NOT EXISTS idx_login_events_created_at ON login_events(created_at DESC);

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
