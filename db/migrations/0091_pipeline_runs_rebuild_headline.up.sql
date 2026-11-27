-- An analyze run's headline before and after a rebuild that a method version
-- or fingerprint change forced (pipeline.rebuild_headline): the agency's
-- on-time rate and average delay over the history the rebuild rewrote, under
-- the default definition, with the preset and method version. NULL on every
-- other run. Written best-effort after the run's row closes, like the rest of
-- this table, so a run can be missing its headline; read it as "this rebuild
-- moved the headline so", never as "no other rebuild happened".
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS rebuild_headline JSONB;
