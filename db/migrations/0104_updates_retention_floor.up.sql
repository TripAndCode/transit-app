-- `retention` records a run of pipeline.retention.prune_updates, which drops
-- ClickHouse `updates` months past the horizon. A drop deletes rows only a
-- replay from R2 brings back, so each run has to be visible on the board.
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe', 'retention'));

-- One row per `updates` month the prune drops, written and committed before
-- the drop, so a run that dies mid-way leaves a row with dropped_at NULL.
-- retention_floor is the first service date analyze may still purge or
-- rebuild for each agency in agency_ids (pipeline.retention.retention_floor):
-- the aggregates of every earlier date are final, since `updates` no longer
-- holds all of their rows.
CREATE TABLE IF NOT EXISTS updates_pruned_months (
    id BIGSERIAL PRIMARY KEY,
    partition_id TEXT NOT NULL CHECK (partition_id ~ '^[0-9]{4}(0[1-9]|1[0-2])$'),
    agency_ids INTEGER[] NOT NULL,
    retention_floor DATE NOT NULL,
    rows BIGINT NOT NULL,
    bytes BIGINT NOT NULL,
    run_id BIGINT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dropped_at TIMESTAMPTZ
);
