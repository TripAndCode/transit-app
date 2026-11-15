-- `retention` records a prune of ClickHouse `updates` partitions past the
-- horizon (pipeline.retention.prune_updates). A drop deletes rows only a
-- replay from R2 brings back, so each one has to be visible on the board.
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe', 'retention'));
