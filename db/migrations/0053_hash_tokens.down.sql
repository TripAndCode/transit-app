-- DESTRUCTIVE: deletes every session and API key row created since the up
-- migration was applied, since none of them has a raw `sid` / `key` to restore.
--
-- Restores the raw-credential primary keys.
--
-- Rows minted while the up migration was in effect carry no raw `sid` / `key`
-- -- that is the entire point of hashing them -- so they cannot be restored and
-- are deleted rather than left as an unkeyable remainder. The practical effect
-- of rolling back is that every session created since the up migration is
-- logged out, and every API key issued since then must be re-provisioned.

DELETE FROM sessions WHERE sid IS NULL;
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_pkey;
ALTER TABLE sessions DROP COLUMN IF EXISTS sid_hash;
ALTER TABLE sessions ALTER COLUMN sid SET NOT NULL;
ALTER TABLE sessions ADD PRIMARY KEY (sid);

DELETE FROM api_keys WHERE key IS NULL;
ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_pkey;
ALTER TABLE api_keys DROP COLUMN IF EXISTS key_hash;
ALTER TABLE api_keys DROP COLUMN IF EXISTS revoked_at;
ALTER TABLE api_keys DROP COLUMN IF EXISTS expires_at;
ALTER TABLE api_keys ALTER COLUMN key SET NOT NULL;
ALTER TABLE api_keys ADD PRIMARY KEY (key);
