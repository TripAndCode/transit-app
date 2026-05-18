DELETE FROM login_events WHERE kind IN ('account_created', 'login_failed');
ALTER TABLE login_events DROP CONSTRAINT IF EXISTS login_events_kind_check;
ALTER TABLE login_events ADD CONSTRAINT login_events_kind_check CHECK (
    kind IN ('login', 'logout', 'role_changed', 'suspended', 'unsuspended', 'deleted')
);
