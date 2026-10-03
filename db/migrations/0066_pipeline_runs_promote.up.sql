-- Promotion copies each closed JST day from ClickHouse `updates_live` into
-- `updates` (pipeline/promote.py). It is recorded as its own kind because a
-- failure there is the one that loses data once the live table's TTL passes.
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static'));
