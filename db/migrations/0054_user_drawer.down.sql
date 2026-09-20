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
DROP INDEX IF EXISTS idx_api_keys_key_hash;
DROP INDEX IF EXISTS idx_api_keys_id;

ALTER TABLE api_keys
    DROP COLUMN IF EXISTS expires_at,
    DROP COLUMN IF EXISTS revoked_at,
    DROP COLUMN IF EXISTS label,
    DROP COLUMN IF EXISTS owner_user_id,
    DROP COLUMN IF EXISTS key_hash,
    DROP COLUMN IF EXISTS id;
