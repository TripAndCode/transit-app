CREATE TABLE IF NOT EXISTS api_keys (
    key         TEXT PRIMARY KEY,
    owner_email TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'pro',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
