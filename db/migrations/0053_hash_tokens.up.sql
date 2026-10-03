-- Session ids and API keys are bearer credentials: whoever holds the string is
-- the user. Storing them verbatim makes any copy of this database -- a backup,
-- a dump handed to a contractor, a `SELECT` by an operator -- directly
-- replayable as a signed-in session. Both tables therefore key on the SHA-256
-- hex digest of the secret; the secret itself is only ever held by the client.
--
-- The digest is produced identically by `api.security.token_hash` and by
-- `encode(sha256(convert_to(<column>,'UTF8')),'hex')` here. `convert_to` rather
-- than a `::bytea` cast because the cast is an I/O conversion that would
-- reinterpret backslash escapes in the stored text.
--
-- The raw `sid` / `key` columns survive this migration so the matching down
-- migration can restore the previous schema without destroying credentials
-- that already exist. Nothing reads or writes them from here on: new rows
-- leave them NULL. Invariant governing the follow-up migration that drops
-- them: they may be dropped once no row that predates this migration is still
-- resolvable, i.e. once every pre-existing session has passed its `expires_at`
-- and every pre-existing API key has been re-issued.

-- Sessions -----------------------------------------------------------------

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS sid_hash TEXT;

UPDATE sessions
   SET sid_hash = encode(sha256(convert_to(sid, 'UTF8')), 'hex')
 WHERE sid_hash IS NULL
   AND sid IS NOT NULL;

-- Two rows can only collide here if their raw sids collided, which the primary
-- key already forbade; the digest is therefore as unique as the column it
-- replaces. Moving the primary key is what lets `sid` become nullable.
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_pkey;
ALTER TABLE sessions ALTER COLUMN sid DROP NOT NULL;
ALTER TABLE sessions ALTER COLUMN sid_hash SET NOT NULL;
ALTER TABLE sessions ADD PRIMARY KEY (sid_hash);

-- API keys -----------------------------------------------------------------

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_hash   TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

UPDATE api_keys
   SET key_hash = encode(sha256(convert_to(key, 'UTF8')), 'hex')
 WHERE key_hash IS NULL
   AND key IS NOT NULL;

ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_pkey;
ALTER TABLE api_keys ALTER COLUMN key DROP NOT NULL;
ALTER TABLE api_keys ALTER COLUMN key_hash SET NOT NULL;
ALTER TABLE api_keys ADD PRIMARY KEY (key_hash);

-- `revoked_at` and `expires_at` are both NULL-means-unlimited: a key is usable
-- while it has not been revoked and has not passed its expiry. Provisioning a
-- key stays a single INSERT of `key_hash` plus `owner_email`; revoking one is a
-- single UPDATE, with no need to locate and delete the row.
