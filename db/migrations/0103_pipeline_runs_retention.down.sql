-- DESTRUCTIVE: deletes every `retention` run row; the narrower CHECK cannot be
-- restored while one exists.
DELETE FROM pipeline_runs WHERE kind = 'retention';
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe'));
