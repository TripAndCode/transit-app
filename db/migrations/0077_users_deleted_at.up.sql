-- An admin soft-delete is terminal, and `suspended_at` alone cannot say so: a
-- suspension is reversible, and lifting it brings the account's API keys back.
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- A row the soft-delete anonymized before this column existed carries the
-- `deleted-{user_id}@local` address with its own id. Its time is that delete's
-- login event, or the row's last update where no event survives.
UPDATE users u
SET deleted_at = COALESCE(
    (SELECT max(e.created_at) FROM login_events e WHERE e.user_id = u.user_id AND e.kind = 'deleted'),
    u.updated_at
)
WHERE u.deleted_at IS NULL AND u.email = 'deleted-' || u.user_id || '@local';

-- A deleted row is always suspended too: the admin guards and the pending
-- approvals count read `suspended_at IS NULL` as an active account, and a
-- deleted row can no longer be suspended through the API. One whose
-- suspension was lifted after the delete is suspended again here.
UPDATE users SET suspended_at = deleted_at WHERE deleted_at IS NOT NULL AND suspended_at IS NULL;

-- The soft-delete now masks and revokes every key the user owns. For a user
-- deleted before that, this does the same to two sets of keys, and to no others:
--
-- 1. Every key whose owner_user_id is that user.
-- 2. Every ownerless (legacy) key whose owner_email matches, in any case, the
--    address in the `before` snapshot of that user's `user.deleted` audit row,
--    and which already existed when that row was written. That snapshot is the
--    only place the original address survives. A key inserted later belongs to
--    whoever holds the address now. An ownerless key of a user deleted with no
--    such audit row cannot be matched and is left as it is.
UPDATE api_keys k
SET owner_email = u.email, revoked_at = COALESCE(k.revoked_at, now())
FROM users u
WHERE k.owner_user_id = u.user_id
  AND u.deleted_at IS NOT NULL
  AND (k.owner_email <> u.email OR k.revoked_at IS NULL);

UPDATE api_keys k
SET owner_email = u.email, revoked_at = COALESCE(k.revoked_at, now())
FROM users u
JOIN admin_audit a
  ON a.action = 'user.deleted' AND a.target_type = 'user' AND a.target_id = u.user_id::text
WHERE u.deleted_at IS NOT NULL
  AND k.owner_user_id IS NULL
  AND lower(k.owner_email) = lower(a.before->>'email')
  AND k.created_at <= a.at;
