CREATE INDEX IF NOT EXISTS idx_users_email_trgm
    ON users USING gin (email gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_users_name_trgm
    ON users USING gin (name gin_trgm_ops);

DROP INDEX IF EXISTS idx_users_name_nfkc_trgm;
DROP INDEX IF EXISTS idx_users_email_nfkc_trgm;
