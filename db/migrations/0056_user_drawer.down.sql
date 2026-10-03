-- DESTRUCTIVE: deletes the login_events audit rows of the five kinds this
-- migration introduced, and drops user_invites and its rows entirely.
--
-- api_keys.id is BIGSERIAL: dropping and later re-adding it (a down/up
-- cycle) restarts the sequence from 1, so any admin-UI reference built from
-- an old id no longer points at the same key -- or at any key at all --
-- after the cycle.
--
-- The rows first: restoring the narrower CHECK while the five kinds this
-- migration introduced are still present fails the constraint outright.
DELETE FROM login_events WHERE kind IN (
    'session_revoked', 'api_key_issued', 'api_key_revoked',
    'invite_created', 'invite_consumed'
);
ALTER TABLE login_events DROP CONSTRAINT IF EXISTS login_events_kind_check;
ALTER TABLE login_events ADD CONSTRAINT login_events_kind_check CHECK (
    kind IN (
        'login', 'logout', 'role_changed', 'suspended', 'unsuspended', 'deleted',
        'account_created', 'login_failed',
        'agency_created', 'agency_updated', 'agency_deleted', 'agency_restored',
        'llm_approved_changed'
    )
);

DROP TABLE IF EXISTS user_invites;

DROP INDEX IF EXISTS idx_api_keys_owner_user_id;
DROP INDEX IF EXISTS idx_api_keys_id;

ALTER TABLE api_keys
    DROP COLUMN IF EXISTS label,
    DROP COLUMN IF EXISTS owner_user_id,
    DROP COLUMN IF EXISTS id;
