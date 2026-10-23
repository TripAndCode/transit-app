-- The admin user list's search (api/routers/admin.py) matches
-- normalize(email, NFKC) and normalize(name, NFKC), so full-width IME input
-- and half-width katakana find their plain forms. The planner uses an
-- expression index only for a predicate on that same expression, so these
-- trigram indexes replace 0052's on the raw columns, which no query reads.
--
-- normalize() is immutable, which is what lets it be indexed, and raises on a
-- server encoding other than UTF8. The image db/Dockerfile builds initializes
-- UTF8 with an en_US.utf8 ctype, under which ILIKE also folds case beyond
-- ASCII.

CREATE INDEX IF NOT EXISTS idx_users_email_nfkc_trgm
    ON users USING gin (normalize(email, NFKC) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_users_name_nfkc_trgm
    ON users USING gin (normalize(name, NFKC) gin_trgm_ops);

DROP INDEX IF EXISTS idx_users_email_trgm;
DROP INDEX IF EXISTS idx_users_name_trgm;
