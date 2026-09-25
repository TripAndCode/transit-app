ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_agency_id_fkey;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_agency_id_fkey
    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id);

CREATE INDEX IF NOT EXISTS idx_login_events_user_id ON login_events(user_id);

DROP INDEX IF EXISTS pipeline_runs_running_started_at_idx;
DROP INDEX IF EXISTS idx_admin_audit_action_at;
DROP INDEX IF EXISTS idx_admin_audit_actor_at;
DROP INDEX IF EXISTS idx_ask_query_log_created_id;
