-- Indexes and FK adjustment for admin/ask/runs query shapes flagged by the
-- 2026-09 static audit.
--
--   - idx_ask_query_log_created_id (created_at DESC, id DESC) serves
--     GET /api/admin/ask/queries (newest-first, id-keyset paginated, with an
--     optional created_at range) and GET /api/admin/ask/funnel (grouped over
--     a created_at range): today only the primary key and
--     (agency_id, created_at) exist, neither of which can answer a plain
--     "newest N, optionally date-bounded" scan without a sort or a full-index
--     scan of every agency.
--   - idx_admin_audit_actor_at (actor_id, at DESC) and
--     idx_admin_audit_action_at (action, at DESC) serve
--     GET /api/admin/audit's `actor=`/`action=` filters, always ordered
--     `at DESC, id DESC` (api/routers/admin.py's ``list_admin_audit``): only
--     target_type/target_id and the bare `at` are indexed today, so an
--     actor- or action-scoped page falls back to scanning the `at`-ordered
--     index row by row instead of seeking straight to the matching rows.
--   - pipeline_runs_running_started_at_idx (started_at) WHERE
--     finished_at IS NULL serves an ops check for runs stuck in `running`
--     with a dead process behind them (see 0059's column comments): every
--     existing pipeline_runs query is bounded by started_at within one day,
--     but a stuck run can be from any day, and the existing
--     pipeline_runs_started_at_idx does not let that search skip finished
--     rows.
--   - idx_login_events_user_id (0009: plain `user_id` btree) is superseded
--     by 0052's idx_login_events_user_created (user_id, created_at DESC),
--     whose leading column already answers a bare `user_id = $1` lookup;
--     the single-column index is now pure write overhead.
--   - pipeline_runs.agency_id's FK had no ON DELETE behavior, unlike every
--     other historical reference this table and login_events use
--     (pipeline_runs.requested_by and login_events.actor_id are both
--     ON DELETE SET NULL): deleting an agency would be blocked by its own
--     run history instead of leaving that history in place with agency_id
--     cleared.

CREATE INDEX IF NOT EXISTS idx_ask_query_log_created_id
    ON ask_query_log (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_actor_at
    ON admin_audit (actor_id, at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_action_at
    ON admin_audit (action, at DESC);

CREATE INDEX IF NOT EXISTS pipeline_runs_running_started_at_idx
    ON pipeline_runs (started_at)
    WHERE finished_at IS NULL;

DROP INDEX IF EXISTS idx_login_events_user_id;

ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_agency_id_fkey;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_agency_id_fkey
    FOREIGN KEY (agency_id) REFERENCES agencies(agency_id) ON DELETE SET NULL;
