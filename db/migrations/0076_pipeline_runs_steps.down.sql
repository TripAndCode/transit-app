-- DESTRUCTIVE: deletes every `update` run row (and, through the cascade, its
-- steps), and folds `unchanged` into `ok`; the narrower CHECKs cannot be
-- restored while either exists.
DROP INDEX IF EXISTS pipeline_runs_static_ok_agency_finished_idx;
DROP INDEX IF EXISTS pipeline_runs_operator_agency_started_idx;
DROP INDEX IF EXISTS pipeline_runs_parent_run_id_idx;
DELETE FROM pipeline_runs WHERE kind = 'update';
ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS parent_run_id;
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static'));
UPDATE pipeline_runs SET status = 'ok' WHERE status = 'unchanged';
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_status_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_status_check
    CHECK (status IN ('running', 'ok', 'skipped', 'error'));
