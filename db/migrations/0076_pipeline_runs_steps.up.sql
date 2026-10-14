-- An operator's run is one row the admin pages watch (the umbrella) plus the
-- step rows the job writes as it goes. `parent_run_id` ties each step to its
-- umbrella, so a page can read a run step by step without matching rows by
-- time: the cron sweep writes rows of the same kinds for the same agency, and
-- a time window cannot tell those apart from a run's own steps.
--
-- `update` is the umbrella kind of the agencies page's chained update
-- (timetable reload, then ingest, then analyze). Its steps keep their own
-- kinds, so the board still draws each one in its usual lane.
--
-- `unchanged` closes a job that ran and found nothing newer to do -- a
-- timetable check that loaded no zip. It is kept apart from `ok` so "last
-- loaded" can mean a load, not the last time someone looked.
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS parent_run_id BIGINT REFERENCES pipeline_runs(run_id) ON DELETE CASCADE;

ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update'));

ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_status_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_status_check
    CHECK (status IN ('running', 'ok', 'skipped', 'error', 'unchanged'));

-- A run's steps, looked up by their umbrella.
CREATE INDEX IF NOT EXISTS pipeline_runs_parent_run_id_idx
    ON pipeline_runs (parent_run_id)
    WHERE parent_run_id IS NOT NULL;

-- Each agency's latest operator-triggered umbrella. Operator rows are a small
-- fraction of the table, which the cron sweep grows by several rows per poke.
CREATE INDEX IF NOT EXISTS pipeline_runs_operator_agency_started_idx
    ON pipeline_runs (agency_id, started_at DESC)
    WHERE parent_run_id IS NULL AND requested_by IS NOT NULL;

-- Each agency's last successful timetable load.
CREATE INDEX IF NOT EXISTS pipeline_runs_static_ok_agency_finished_idx
    ON pipeline_runs (agency_id, finished_at DESC)
    WHERE kind = 'static' AND status = 'ok';
