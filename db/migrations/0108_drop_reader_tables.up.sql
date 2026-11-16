-- Readers use the app without an account; only the deployment's admins sign
-- in. This removes what the reader account platform stored and nothing reads
-- any more: saved filter presets, BYOK LLM keys, invites, API keys, the
-- reader accounts themselves with their sign-in history, the API-key flag on
-- the per-user usage counts, and the overrides of two retired kill switches.
--
-- Every statement that loses data is a top-level DROP TABLE, DELETE FROM or
-- ALTER TABLE ... DROP COLUMN, so `db.migrate.data_losing_tables` names each
-- table and `scripts/dev_upgrade.py` waits for the owner to allow this
-- version. The down recreates the dropped tables empty; none of the rows
-- removed here come back.

-- Taken first, so that a usage flush running beside this migration waits
-- here instead of holding the table while the account deletes below, which
-- cascade into it, wait on that flush.
LOCK TABLE user_activity_daily IN ACCESS EXCLUSIVE MODE;

DROP TABLE IF EXISTS filter_presets;
DROP TABLE IF EXISTS user_llm_keys;
DROP TABLE IF EXISTS user_invites;
DROP TABLE IF EXISTS api_keys;
-- Its trigger went with the table; the function is left without a caller.
DROP FUNCTION IF EXISTS api_keys_fill_key_hash();

-- A reader account is a 'user' row that no admin-only record attributes an
-- action to. An admin who was later demoted is still named by what they did
-- (admin_audit, the other admin-only actor columns, and login_events written
-- about another account), so that row stays and the attribution survives.
-- The set is read once, before any delete changes what it is read from.
CREATE TEMP TABLE reader_users ON COMMIT DROP AS
SELECT u.user_id
  FROM users u
 WHERE u.role = 'user'
   AND NOT EXISTS (SELECT 1 FROM admin_audit a WHERE a.actor_id = u.user_id)
   AND NOT EXISTS (SELECT 1 FROM feature_flags f WHERE f.updated_by = u.user_id)
   AND NOT EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.requested_by = u.user_id)
   AND NOT EXISTS (SELECT 1 FROM admin_alert_acks k WHERE k.acked_by = u.user_id)
   AND NOT EXISTS (SELECT 1 FROM snapshots s WHERE s.created_by = u.user_id)
   AND NOT EXISTS (
       SELECT 1 FROM login_events e
        WHERE e.actor_id = u.user_id AND e.user_id IS DISTINCT FROM u.user_id
   );

-- login_events.user_id is ON DELETE SET NULL, which would keep each event's
-- address and browser with no account left to erase them through, so a
-- reader's events go first. Deleting the account cascades to its sessions,
-- OAuth identities, usage counts and investigations.
DELETE FROM login_events WHERE user_id IN (SELECT user_id FROM reader_users);
DELETE FROM users WHERE user_id IN (SELECT user_id FROM reader_users);

-- via_api_key is part of the usage counts' unique key, so a day's requests
-- through an API key and through a session are two rows. Each such pair is
-- summed into one before the column goes.
UPDATE user_activity_daily s
   SET requests = s.requests + k.requests,
       errors   = s.errors + k.errors
  FROM user_activity_daily k
 WHERE NOT s.via_api_key AND k.via_api_key
   AND (s.user_id, s.day, s.route, s.method, COALESCE(s.agency_id, 0))
     = (k.user_id, k.day, k.route, k.method, COALESCE(k.agency_id, 0));

DELETE FROM user_activity_daily k
 WHERE k.via_api_key
   AND EXISTS (
       SELECT 1 FROM user_activity_daily s
        WHERE NOT s.via_api_key
          AND (s.user_id, s.day, s.route, s.method, COALESCE(s.agency_id, 0))
            = (k.user_id, k.day, k.route, k.method, COALESCE(k.agency_id, 0))
   );

DROP INDEX IF EXISTS user_activity_daily_key;
ALTER TABLE user_activity_daily DROP COLUMN IF EXISTS via_api_key;

-- The upsert's arbiter (api/activity.py), whose ON CONFLICT names these
-- columns exactly. COALESCE because PG14 has no NULLS NOT DISTINCT and a NULL
-- agency_id would otherwise never conflict; agency ids start at 1.
CREATE UNIQUE INDEX IF NOT EXISTS user_activity_daily_key
    ON user_activity_daily (user_id, day, route, method, (COALESCE(agency_id, 0)));

-- Overrides of switches `pipeline.flags.REGISTRY` no longer has; nothing
-- reads them.
DELETE FROM feature_flags WHERE key IN ('login_required', 'copilot_insight_enabled');
