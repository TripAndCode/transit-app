-- `probe` records one agency's unattended RT field-coverage probe
-- (pipeline/rt_coverage_reprobe.py): `ok` when a verdict was written to
-- `rt_field_coverage_probes`, `skipped` when the poll was too thin to count
-- or the agency's feed was repointed mid-probe, `error` when the feed could
-- not be fetched or answered with something that is not a GTFS-RT feed. A
-- lapsed verdict blanks every report built on the RT-optional fields, so
-- whether the renewal ran has to be visible on the board.
ALTER TABLE pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_kind_check;
ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_kind_check
    CHECK (kind IN ('ingest', 'promote', 'analyze', 'weather', 'static', 'update', 'probe'));
