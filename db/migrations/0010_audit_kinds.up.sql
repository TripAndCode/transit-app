-- Widen login_events.kind to cover account_created and login_failed.
-- account_created: first time a user row is INSERTed (distinct from "first login").
-- login_failed:    OAuth callback aborted before a session is minted (bad state,
--                  unverified email, no email returned, provider error).
ALTER TABLE login_events DROP CONSTRAINT IF EXISTS login_events_kind_check;
ALTER TABLE login_events ADD CONSTRAINT login_events_kind_check CHECK (
    kind IN (
        'login', 'logout', 'role_changed', 'suspended', 'unsuspended', 'deleted',
        'account_created', 'login_failed'
    )
);
