-- Password hash for a single local/break-glass admin account (DEFAULT_ADMIN_USERNAME/
-- DEFAULT_ADMIN_PASSWORD env vars). NULL for every OAuth-only user.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
