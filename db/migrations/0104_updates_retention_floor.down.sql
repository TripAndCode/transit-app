-- DESTRUCTIVE: drops updates_pruned_months, the only record of each agency's retention floor. analyze's next full rebuild would then purge the aggregates of every month the prune dropped, which no rebuild restores.
-- DESTRUCTIVE: deletes every `retention` run row; the narrower CHECK cannot be restored while one exists.
DROP TABLE IF EXISTS updates_pruned_months;
DELETE FROM pipeline_runs WHERE kind = 'retention';
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe'));
