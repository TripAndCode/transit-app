-- idx_login_events_actor_created (actor_id, created_at DESC) serves every
-- lookup of the login_events rows a user wrote as the actor rather than the
-- subject:
--
--   - the account export's `admin_account_events` query
--     (pipeline/account_export.py), `actor_id = $1` ordered by created_at;
--   - account erasure's `UPDATE login_events ... WHERE actor_id = $1`
--     (pipeline/account_erasure.py), and the ON DELETE SET NULL on actor_id
--     that deleting the users row then fires.
--
-- No other index on the table covers actor_id, so each of those reads the
-- whole table. The columns mirror 0060's idx_admin_audit_actor_at.
-- Partial on `actor_id IS NOT NULL`: rows with no actor (a failed sign-in, an
-- actor whose own row was since deleted) match none of these lookups.

CREATE INDEX IF NOT EXISTS idx_login_events_actor_created
    ON login_events (actor_id, created_at DESC)
    WHERE actor_id IS NOT NULL;
