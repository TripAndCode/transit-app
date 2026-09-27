-- Pre-builds, without blocking writes, the indexes that pending migrations
-- would otherwise build on tables that already hold data.
--
-- `migrate up` runs each migration inside a transaction, where a plain CREATE
-- INDEX holds a lock that blocks every INSERT/UPDATE/DELETE on the table for
-- the whole build. Each statement here is its migration's own, plus
-- CONCURRENTLY; the migration's `IF NOT EXISTS` then finds the index already
-- built and skips it. Indexes on tables the same release creates are left to
-- their migrations: those tables start empty.
--
-- When: before promoting a release whose pending migrations include any of
-- the versions guarded below, against the database that release will migrate.
-- Unnecessary for a fresh or throwaway database.
--
-- How: with psql in its default autocommit mode, never inside BEGIN or with
-- -1/--single-transaction -- CREATE INDEX CONCURRENTLY refuses to run in a
-- transaction block:
--
--     psql "$DATABASE_URL" -f scripts/prebuild_indexes_concurrently.sql
--
-- Idempotent: each index is built only if missing, and only while the
-- migration that defines it is still pending, so a run after the release has
-- shipped changes nothing -- in particular it cannot bring back an index a
-- later migration dropped. Each build first waits for the transactions open
-- when it started to finish, so a long-running job can delay it; it blocks
-- neither reads nor writes meanwhile.
--
-- A CONCURRENTLY build that fails or is interrupted leaves the index behind
-- marked INVALID: the planner never uses it, yet it exists by name, so a rerun
-- of this script and the migration's IF NOT EXISTS both skip it silently.
-- After any error, list invalid indexes:
--
--     SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
--
-- and for each one this script builds, run `DROP INDEX CONCURRENTLY <name>;`
-- then rerun the script. Promote only once that query lists none of them.

\set ON_ERROR_STOP on

SELECT
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '0052') AS pending_0052,
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '0054') AS pending_0054,
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '0060') AS pending_0060,
    NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '0062') AS pending_0062
\gset

-- 0054 drops idx_ask_conversations_user_agency_updated in the same `migrate up`
-- that runs 0052. It is built here anyway: otherwise 0052 builds it inside its
-- transaction, blocking writes for the whole build, while 0054's drop is quick.
\if :pending_0052
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_login_events_user_created
    ON login_events (user_id, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ask_conversations_user_agency_updated
    ON ask_conversations (user_id, agency_id, updated_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_email_trgm
    ON users USING gin (email gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_name_trgm
    ON users USING gin (name gin_trgm_ops);
\endif

\if :pending_0054
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ask_conversations_user_agency_pinned_updated
    ON ask_conversations (user_id, agency_id, pinned DESC, updated_at DESC);
\endif

\if :pending_0060
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ask_query_log_created_id
    ON ask_query_log (created_at DESC, id DESC);
\endif

-- Built before 0053 runs, these also spare a release that predates 0053 the
-- stretch between 0053 dropping the raw-value primary keys and 0062 restoring
-- an index on those columns.
\if :pending_0062
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_sessions_sid
    ON sessions (sid) WHERE sid IS NOT NULL;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_api_keys_key
    ON api_keys (key) WHERE key IS NOT NULL;
\endif
