-- Unified admin-action audit trail, distinct from login_events (user/session
-- lifecycle). Every admin mutation records one row here with a before/after
-- snapshot so /admin/audit can render a diff; see api/admin_audit.py.
CREATE TABLE IF NOT EXISTS admin_audit (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id    INT REFERENCES users(user_id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT,
    before      JSONB,
    after       JSONB,
    reason      TEXT,
    ip          INET
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_at ON admin_audit (at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit (target_type, target_id);
