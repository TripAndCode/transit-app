-- `probe` records one agency's unattended RT field-coverage probe
-- (pipeline/rt_coverage_reprobe.py, whose module docstring lists what each
-- status means). A lapsed verdict blanks every report built on the
-- RT-optional fields, so whether the renewal ran has to be visible on the
-- board.
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe'));

-- Each agency's latest probe run, which spaces out the retries after an
-- attempt that wrote no verdict. Probe rows are a small share of a table the
-- cron sweep grows by several rows per poke.
CREATE INDEX IF NOT EXISTS pipeline_runs_probe_agency_started_idx
    ON pipeline_runs (agency_id, started_at DESC)
    WHERE kind = 'probe';
